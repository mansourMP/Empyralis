from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from server_modules import sage_agent_runtime_service


def _run(coro):
    return asyncio.run(coro)


class SageAgentRuntimeGatingTests(unittest.TestCase):
    def test_rejects_empty_message(self):
        with self.assertRaises(ValueError) as ctx:
            _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="",
            ))
        self.assertIn("message", str(ctx.exception).lower())

    def test_rejects_empty_workspace_id(self):
        with self.assertRaises(ValueError) as ctx:
            _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="",
                message="hello",
            ))
        self.assertIn("workspace_id", str(ctx.exception).lower())

    def test_rejects_non_owner_sage_mode(self):
        with self.assertRaises(ValueError) as ctx:
            _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="hello",
                mode="customer_live",
            ))
        self.assertIn("mode", str(ctx.exception).lower())


class SageAgentRuntimeContextLoadingTests(unittest.TestCase):
    def test_load_context_files_includes_extended_context_files_and_memory_manifest(self):
        with patch(
            "server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files",
            return_value={
                "SOUL.md": "# Sage\n\n- Custom identity.",
                "GOALS.md": "# Goals\n\n- Ship the phone app.",
                "memory/files/architecture-notes.md": "# Architecture Notes\n\nReference comparison notes.",
            },
        ):
            text = sage_agent_runtime_service._load_context_files(workspace_id="ws-1")

        self.assertIn("SOUL.md", text)
        self.assertIn("GOALS.md", text)
        self.assertIn("Root Memory Index", text)
        self.assertIn("memory/files/architecture-notes.md", text)

    def test_load_context_files_skips_default_placeholders(self):
        with patch(
            "server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files",
            return_value={
                "SOUL.md": sage_agent_runtime_service.workspace_context.DEFAULT_CONTEXT_FILE_CONTENTS["SOUL.md"],
            },
        ):
            text = sage_agent_runtime_service._load_context_files(workspace_id="ws-1")

        self.assertEqual(text, "")

    def test_loads_profile_context(self):
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
            mock_profile.return_value = {
                "profile": {
                    "user_name": "Mansur",
                    "identity_summary": "Lead developer",
                    "communication_style": "",
                    "recurring_responsibility": "",
                    "standing_rules": [],
                }
            }
            mock_files.return_value = {}
            mock_mem.return_value = ""
            mock_provider.return_value = ("deepseek", {"api_key": "test-key"})
            mock_generate.return_value = ("Reply", {"model": "d"}, "deepseek", "")

            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="hello",
            ))

            self.assertIn("sage_profile", result["used_context"])

    def test_loads_memory_context(self):
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
            mock_mem.return_value = "Sage memory: remembers timezone."
            mock_provider.return_value = ("deepseek", {"api_key": "test-key"})
            mock_generate.return_value = ("Reply", {}, "deepseek", "")

            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="hello",
            ))

            self.assertIn("sage_memory", result["used_context"])

    def test_loads_context_files(self):
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
            mock_files.return_value = {"SOUL.md": "You are Sage.", "USER.md": "Name: Test"}
            mock_mem.return_value = ""
            mock_provider.return_value = ("deepseek", {"api_key": "test-key"})
            mock_generate.return_value = ("Reply", {}, "deepseek", "")

            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="hello",
            ))

            self.assertIn("workspace_context_files", result["used_context"])

    def test_loads_heartbeat_context(self):
        heartbeat_data = {
            "bootstrap": {"complete": True},
            "queue_overview": {"running_now_count": 1, "queued_count": 0, "blocked_on_approval_count": 0, "pending_wakeup_count": 0},
            "reminders": {"count": 0},
        }
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile") as mock_profile,
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files") as mock_files,
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block") as mock_mem,
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value=heartbeat_data)),
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
            mock_generate.return_value = ("Reply", {}, "deepseek", "")

            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="hello",
            ))

            self.assertIn("sage_heartbeat", result["used_context"])

    def test_loads_safe_skill_catalog(self):
        from server_modules.skill_registry import SkillDefinition

        safe_skill = SkillDefinition(
            id="web-search", label="Web Search", description="Search the web",
            permission_label="web", execution_mode="live", action_class="read",
            connector_scopes=(), trigger_terms=("search",),
        )
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile") as mock_profile,
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files") as mock_files,
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block") as mock_mem,
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[safe_skill]),
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
            mock_generate.return_value = ("Reply", {}, "deepseek", "")

            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="hello",
            ))

            self.assertIn("sage_skills", result["used_context"])
            self.assertTrue(any(t["id"] == "web-search" for t in result["available_tools"]))


class SageAgentRuntimeSafetyTests(unittest.TestCase):
    def _setup_mocks(self, *, profile_overrides=None, skills=None, memory_return="", files_return=None):
        mocks = {}
        defaults = {"user_name": "", "identity_summary": "", "communication_style": "", "recurring_responsibility": "", "standing_rules": []}
        if profile_overrides:
            defaults.update(profile_overrides)
        mocks["profile"] = patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": defaults})
        mocks["files"] = patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value=files_return or {})
        mocks["memory"] = patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=memory_return)
        mocks["heartbeat"] = patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={}))
        mocks["skills"] = patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=skills or [])
        mocks["provider"] = patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", return_value=("deepseek", {"api_key": "test"}))
        mocks["generate"] = patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback", return_value=("Reply", {"model": "d"}, "deepseek", ""))
        mocks["persist"] = patch("server_modules.sage_agent_runtime_service.persist_interaction")
        mocks["activity"] = patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock())
        mocks["audit"] = patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event")
        mocks["approval"] = patch(
            "server_modules.sage_agent_runtime_service._create_approval_for_blocked_action",
            return_value={
                "type": "tool_action",
                "skill_id": "email-access",
                "label": "Email Access",
                "action_class": "write",
                "reason": "Requires explicit owner approval before write/execute action.",
                "approval_token": "sap_test_token_1234",
                "status": "pending",
                "action": "channel_send_draft",
                "description": "Approve a write action",
                "expires_at": "2026-05-11T12:00:00Z",
            },
        )
        return mocks

    def test_excludes_critical_restricted_memory(self):
        mocks = self._setup_mocks(memory_return="Safe facts only")
        with (
            mocks["profile"], mocks["files"], mocks["memory"] as mock_mem,
            mocks["heartbeat"], mocks["skills"], mocks["provider"],
            mocks["generate"], mocks["persist"], mocks["activity"], mocks["audit"],
        ):
            _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="hello",
            ))

            call_kwargs = mock_mem.call_args
            if call_kwargs[1]:
                self.assertFalse(call_kwargs[1].get("include_restricted", True))
            else:
                self.assertFalse(call_kwargs[0].get("include_restricted", True) if len(call_kwargs[0]) > 0 else True)

    def test_secret_redaction_applied_to_prompt(self):
        mocks = self._setup_mocks()
        with (
            mocks["profile"], mocks["files"], mocks["memory"], mocks["heartbeat"],
            mocks["skills"], mocks["provider"],
            mocks["generate"] as mock_gen,
            mocks["persist"], mocks["activity"], mocks["audit"],
        ):
            _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="hello",
            ))

            system_prompt = mock_gen.call_args[0][3]
            self.assertNotIn("sk-", system_prompt)
            self.assertIn("what can you do", system_prompt)
            self.assertIn("user's personal AI assistant", system_prompt)
            self.assertIn("never a bullet list", system_prompt)
            self.assertIn("Never write XML, tool_calls, invoke tags", system_prompt)

    def test_chat_only_fullwidth_dsml_reply_is_sanitized_before_persisting(self):
        dsml_reply = (
            "Let me check.\n"
            "<｜｜DSML｜｜tool_calls>"
            "<｜｜DSML｜｜invoke name=\"bash\">"
            "<｜｜DSML｜｜parameter name=\"command\" string=\"true\">system_profiler</｜｜DSML｜｜parameter>"
            "</｜｜DSML｜｜invoke>"
        )
        mocks = self._setup_mocks()
        mocks["generate"] = patch(
            "server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback",
            return_value=(dsml_reply, {"model": "deepseek-chat"}, "deepseek", ""),
        )
        with (
            mocks["profile"], mocks["files"], mocks["memory"], mocks["heartbeat"],
            mocks["skills"], mocks["provider"],
            mocks["generate"], mocks["persist"] as mock_persist, mocks["activity"], mocks["audit"],
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="hello",
            ))

            self.assertEqual(result["message"], "Let me check.")
            self.assertNotIn("DSML", result["message"])
            self.assertNotIn("system_profiler", result["message"])
            persisted_reply = mock_persist.call_args.kwargs["assistant_reply"]
            self.assertEqual(persisted_reply, "Let me check.")
            self.assertNotIn("DSML", persisted_reply)

    def test_write_skill_terms_do_not_preempt_model(self):
        from server_modules.skill_registry import SkillDefinition

        dangerous = SkillDefinition(
            id="email-access", label="Email Access", description="Send email",
            permission_label="email", execution_mode="manual", action_class="write",
            connector_scopes=("email",), trigger_terms=("send email", "email"),
            requires_approval=True,
        )
        mocks = self._setup_mocks(skills=[dangerous])
        with (
            mocks["profile"], mocks["files"], mocks["memory"], mocks["heartbeat"],
            mocks["skills"], mocks["provider"],
            mocks["generate"], mocks["persist"], mocks["activity"],
            mocks["audit"] as mock_audit, mocks["approval"],
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="send email to boss",
            ))

            self.assertEqual(result["message"], "Reply")
            self.assertEqual(result["blocked_tools"], [])
            self.assertEqual(len(result["available_tools"]), 0)
            self.assertEqual(result["approvals_required"], [])

    def test_write_skill_does_not_create_keyword_approval_card(self):
        from server_modules.skill_registry import SkillDefinition

        dangerous = SkillDefinition(
            id="email-access", label="Email Access", description="Send email",
            permission_label="email", execution_mode="manual", action_class="write",
            connector_scopes=("email",), trigger_terms=("send email",),
            requires_approval=True,
        )
        mocks = self._setup_mocks(skills=[dangerous])
        with (
            mocks["profile"], mocks["files"], mocks["memory"], mocks["heartbeat"],
            mocks["skills"], mocks["provider"],
            mocks["generate"], mocks["persist"], mocks["activity"], mocks["audit"], mocks["approval"],
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="send email to boss",
                current_user={"user_id": "owner-1"},
            ))

            self.assertEqual(result["message"], "Reply")
            self.assertEqual(result["blocked_tools"], [])
            self.assertEqual(result["approvals_required"], [])
            self.assertEqual(result["action_execution_mode"], "text_only")

    def test_kill_switch_does_not_fire_from_keyword_scan(self):
        from server_modules import kill_switch_gate
        from server_modules.skill_registry import SkillDefinition

        dangerous = SkillDefinition(
            id="email-access", label="Email Access", description="Send email",
            permission_label="email", execution_mode="manual", action_class="write",
            connector_scopes=("email",), trigger_terms=("send email",),
            requires_approval=True,
        )
        mocks = self._setup_mocks(skills=[dangerous])
        with (
            patch.object(
                kill_switch_gate.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                side_effect=lambda **kwargs: {
                    "ok": True,
                    "decision": "allow",
                    "next_action": kwargs.get("operation"),
                },
            ),
            patch.object(
                kill_switch_gate.rust_runtime_kernel_client,
                "enforce_kernel_decision",
                side_effect=lambda _command, decision: decision,
            ),
        ):
            kill_switch_gate.set_kill_switch("agent:sage_main_agent", active=True)
            try:
                with (
                    mocks["profile"], mocks["files"], mocks["memory"], mocks["heartbeat"],
                    mocks["skills"], mocks["provider"],
                    mocks["generate"], mocks["persist"], mocks["activity"], mocks["audit"],
                    mocks["approval"] as mock_approval,
                ):
                    result = _run(sage_agent_runtime_service.handle_sage_chat(
                        workspace_id="ws-1",
                        message="send email to boss",
                        current_user={"user_id": "owner-1"},
                    ))

                    self.assertEqual(result["message"], "Reply")
                    self.assertEqual(result["blocked_tools"], [])
                    self.assertEqual(result["approvals_required"], [])
                    mock_approval.assert_not_called()
            finally:
                kill_switch_gate.clear_kill_switch("agent:sage_main_agent")

    def test_execute_skill_terms_do_not_preempt_model(self):
        from server_modules.skill_registry import SkillDefinition

        dangerous = SkillDefinition(
            id="task-runner", label="Task Runner", description="Run tasks",
            permission_label="task", execution_mode="manual", action_class="execute",
            connector_scopes=(), trigger_terms=("run", "execute"),
            requires_approval=True,
        )
        mocks = self._setup_mocks(skills=[dangerous])
        with (
            mocks["profile"], mocks["files"], mocks["memory"], mocks["heartbeat"],
            mocks["skills"], mocks["provider"],
            mocks["generate"], mocks["persist"], mocks["activity"], mocks["audit"], mocks["approval"],
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="run the deployment script",
            ))

            self.assertEqual(result["message"], "Reply")
            self.assertEqual(result["blocked_tools"], [])
            self.assertEqual(result["approvals_required"], [])

    def test_keyword_scan_does_not_build_decision_payload_with_secret_like_text(self):
        from server_modules.skill_registry import SkillDefinition

        dangerous = SkillDefinition(
            id="email-access", label="Email Access", description="Send email",
            permission_label="email", execution_mode="manual", action_class="write",
            connector_scopes=("email",), trigger_terms=("send email",),
            requires_approval=True,
        )
        mocks = self._setup_mocks(skills=[dangerous])
        with (
            mocks["profile"], mocks["files"], mocks["memory"], mocks["heartbeat"],
            mocks["skills"], mocks["provider"],
            mocks["generate"], mocks["persist"], mocks["activity"], mocks["audit"], mocks["approval"],
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="send email with sk-proj-this-should-not-leak",
            ))

            self.assertEqual(result["message"], "Reply")
            self.assertEqual(result["blocked_tools"], [])
            self.assertEqual(result["approvals_required"], [])

    def test_keyword_scan_creates_no_surface_approval_tokens(self):
        from server_modules.skill_registry import SkillDefinition

        dangerous = SkillDefinition(
            id="email-access", label="Email Access", description="Send email",
            permission_label="email", execution_mode="manual", action_class="write",
            connector_scopes=("email",), trigger_terms=("send email",),
            requires_approval=True,
        )
        mocks = self._setup_mocks(skills=[dangerous])
        with (
            mocks["profile"], mocks["files"], mocks["memory"], mocks["heartbeat"],
            mocks["skills"], mocks["provider"], mocks["generate"], mocks["persist"],
            mocks["activity"], mocks["audit"], mocks["approval"],
        ):
            chat_result = _run(
                sage_agent_runtime_service.handle_sage_chat(
                    workspace_id="ws-1",
                    message="send email now",
                    surface="chat",
                )
            )
            mobile_result = _run(
                sage_agent_runtime_service.handle_sage_chat(
                    workspace_id="ws-1",
                    message="send email now",
                    surface="mobile",
                )
            )

            self.assertEqual(chat_result["approvals_required"], [])
            self.assertEqual(mobile_result["approvals_required"], [])


class SageAgentRuntimePersistenceTests(unittest.TestCase):
    def test_persist_interaction_called(self):
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile") as mock_profile,
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files") as mock_files,
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block") as mock_mem,
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider") as mock_provider,
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback") as mock_generate,
            patch("server_modules.sage_agent_runtime_service.persist_interaction") as mock_persist,
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            mock_profile.return_value = {"profile": {"user_name": "", "identity_summary": "", "communication_style": "", "recurring_responsibility": "", "standing_rules": []}}
            mock_files.return_value = {}
            mock_mem.return_value = ""
            mock_provider.return_value = ("deepseek", {"api_key": "test-key"})
            mock_generate.return_value = ("Reply", {}, "deepseek", "")

            _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="hello",
            ))

            mock_persist.assert_called_once()
            kwargs = mock_persist.call_args.kwargs
            subject = kwargs["subject"]
            self.assertEqual(subject.surface_kind, "direct_chat")
            self.assertEqual(kwargs["user_message"], "hello")
            self.assertEqual(kwargs["assistant_reply"], "Reply")


class SageAgentRuntimeAuditTests(unittest.TestCase):
    def test_activity_event_emitted(self):
        # The action loop must itself succeed (not merely fall back to a
        # successful generate_chat_reply_with_provider_fallback mock) — the
        # 2026-07-09 attribution fix classifies the ledger event as failed
        # whenever action_result carries an error, so a genuinely successful
        # stream is required to test the "completed" event honestly.
        stream_events = [{"type": "final", "payload": {"reply": "Reply", "actions": [], "error": ""}}]
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile") as mock_profile,
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files") as mock_files,
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block") as mock_mem,
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider") as mock_provider,
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability", return_value={"runtime_ok": True, "local_gateway_online": True}),
            patch("server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat", return_value=iter(stream_events)),
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()) as mock_activity,
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            mock_profile.return_value = {"profile": {"user_name": "", "identity_summary": "", "communication_style": "", "recurring_responsibility": "", "standing_rules": []}}
            mock_files.return_value = {}
            mock_mem.return_value = ""
            mock_provider.return_value = ("deepseek", {"api_key": "test-key"})

            _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", tenant_id="t-1", message="hello",
                current_user={"user_id": "u-1"},
            ))

            sage_activity_calls = [
                call
                for call in mock_activity.await_args_list
                if call.kwargs.get("event_class") == "sage_activity"
                and call.kwargs.get("action") == "sage_chat.completed"
            ]
            self.assertEqual(len(sage_activity_calls), 1)
            kwargs = sage_activity_calls[0].kwargs
            self.assertEqual(kwargs["event_class"], "sage_activity")
            self.assertEqual(kwargs["action"], "sage_chat.completed")
            self.assertEqual(kwargs["status"], "logged")
            self.assertEqual(kwargs["workspace_id"], "ws-1")
            self.assertEqual(kwargs["tenant_id"], "t-1")

    def test_activity_event_emitted_as_failed_on_provider_error(self):
        """2026-07-09 attribution fix: a turn that fails must ledger as
        failed/error, never silently as "completed"/"logged" — this is the
        exact bug that made a failed conversation invisible on Overview
        while the Work tab showed it happened. The action loop synthesizes a
        user-facing reply (fix #1's honest classify_error text) even when
        the underlying provider call failed — action_result still carries
        that failure in its "error" key, which is what the ledger must key
        off, not whether some reply text exists."""
        stream_events = [{
            "type": "final",
            "payload": {"reply": "The AI connection needs attention.", "actions": [], "error": "provider_generation_failed"},
        }]
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", return_value=("deepseek", {"api_key": "test-key"})),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability", return_value={"runtime_ok": True, "local_gateway_online": True}),
            patch("server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat", return_value=iter(stream_events)),
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()) as mock_activity,
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", tenant_id="t-1", message="hello",
                current_user={"user_id": "u-1"},
            ))

            failed_calls = [
                call for call in mock_activity.await_args_list
                if call.kwargs.get("action") == "sage_chat.failed"
            ]
            self.assertEqual(len(failed_calls), 1)
            kwargs = failed_calls[0].kwargs
            self.assertEqual(kwargs["status"], "error")
            self.assertEqual(kwargs["title"], "Sage chat failed")
            completed_calls = [
                call for call in mock_activity.await_args_list
                if call.kwargs.get("action") == "sage_chat.completed"
            ]
            self.assertEqual(len(completed_calls), 0)

    def test_security_audit_event_emitted(self):
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
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event") as mock_audit,
        ):
            mock_profile.return_value = {"profile": {"user_name": "", "identity_summary": "", "communication_style": "", "recurring_responsibility": "", "standing_rules": []}}
            mock_files.return_value = {}
            mock_mem.return_value = ""
            mock_provider.return_value = ("deepseek", {"api_key": "test-key"})
            mock_generate.return_value = ("Reply", {}, "deepseek", "")

            _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", tenant_id="t-1", message="hello",
                current_user={"user_id": "u-1", "email": "test@test.com"},
            ))

            self.assertTrue(mock_audit.called)
            audit_calls = [c for c in mock_audit.call_args_list
                           if c.kwargs.get("action") == "sage_chat.completed"]
            self.assertEqual(len(audit_calls), 1)
            self.assertEqual(audit_calls[0].kwargs["status"], "success")

    def test_keyword_terms_do_not_emit_blocked_tool_audit(self):
        from server_modules.skill_registry import SkillDefinition

        dangerous = SkillDefinition(
            id="email-access", label="Email", description="Send",
            permission_label="email", execution_mode="manual", action_class="write",
            connector_scopes=("email",), trigger_terms=("send email",),
            requires_approval=True,
        )
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile") as mock_profile,
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files") as mock_files,
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block") as mock_mem,
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[dangerous]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider") as mock_provider,
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback") as mock_generate,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event") as mock_audit,
        ):
            mock_profile.return_value = {"profile": {"user_name": "", "identity_summary": "", "communication_style": "", "recurring_responsibility": "", "standing_rules": []}}
            mock_files.return_value = {}
            mock_mem.return_value = ""
            mock_provider.return_value = ("deepseek", {"api_key": "test-key"})
            mock_generate.return_value = ("Reply", {}, "deepseek", "")

            _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="send email now",
            ))

            blocked_calls = [c for c in mock_audit.call_args_list
                             if c.kwargs.get("action") == "sage_chat.tool_blocked"]
            self.assertEqual(blocked_calls, [])


class SageAgentRuntimeResultShapeTests(unittest.TestCase):
    @staticmethod
    def _trace(event_type, *, tool_call_id=None, data=None):
        return {
            "type": "trace",
            "payload": {
                "event_type": event_type,
                "tool_call_id": tool_call_id,
                "data": dict(data or {}),
            },
        }

    def test_returns_full_contract(self):
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
            mock_provider.return_value = ("openai", {"api_key": "test-key"})
            mock_generate.return_value = ("Hello there", {"model": "gpt-4o"}, "openai", "")

            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="hi",
            ))

            self.assertEqual(result["message"], "Hello there")
            self.assertIsNone(result["error"])
            self.assertIsInstance(result["used_context"], list)
            self.assertIsInstance(result["tool_calls"], list)
            self.assertIsInstance(result["available_tools"], list)
            self.assertIsInstance(result["blocked_tools"], list)
            self.assertIsInstance(result["approvals_required"], list)
            self.assertIsInstance(result["memory_updates"], list)
            self.assertIsNotNone(result["trace_id"])
            self.assertEqual(result["provider"], "openai")
            self.assertEqual(result["model"], "gpt-4o")

    def test_main_sage_chat_executes_web_search_tool(self):
        stream_events = [
            self._trace(
                "tool.started",
                tool_call_id="call-search-1",
                data={"tool_name": "web__search", "args_preview": {"query": "OpenClaw browser docs"}},
            ),
            self._trace(
                "tool.result",
                tool_call_id="call-search-1",
                data={"status": "ok", "summary": "1. Result\nURL: https://example.com\nSnippet: Found it."},
            ),
            {"type": "chunk", "delta": "I found the OpenClaw browser docs."},
            {"type": "final", "payload": {"reply": "I found the OpenClaw browser docs.", "actions": [], "error": ""}},
        ]
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback") as mock_generate,
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability", return_value={"runtime_ok": False}),
            patch("server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat", return_value=iter(stream_events)) as mock_stream,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="search the web for OpenClaw browser docs",
            ))

        self.assertEqual(result["action_execution_mode"], "tools_executed")
        self.assertEqual(result["action_loop_version"], "v3")
        self.assertEqual(result["tool_calls"][0]["name"], "web__search")
        self.assertIn("OpenClaw browser docs", result["tool_calls"][0]["arguments"]["query"])
        self.assertEqual(result["message"], "I found the OpenClaw browser docs.")
        self.assertFalse(mock_generate.called)
        self.assertTrue(mock_stream.called)
        stream_kwargs = mock_stream.call_args.kwargs
        self.assertIn("You're Sage", stream_kwargs["system_prompt"])
        self.assertEqual(stream_kwargs["session_ctx"]["agent_turn_request"]["policy_context"]["agent_scope"], "sage")
        self.assertEqual(stream_kwargs["session_ctx"]["agent_turn_request"]["policy_context"]["agent_id"], "sage_main_agent")

    def test_main_sage_chat_operator_loop_does_not_block_backend_event_loop(self):
        stream_events = [
            {"type": "final", "payload": {"reply": "done", "actions": [], "error": ""}},
        ]
        timeline: List[tuple[str, float]] = []

        def blocking_stream(*_args, **_kwargs):
            timeline.append(("stream_start", time.monotonic()))
            time.sleep(0.15)
            timeline.append(("stream_end", time.monotonic()))
            return iter(stream_events)

        async def run_scenario():
            async def ticker():
                await asyncio.sleep(0.02)
                timeline.append(("tick", time.monotonic()))

            chat_task = asyncio.create_task(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="run: echo hello from hardware",
            ))
            tick_task = asyncio.create_task(ticker())
            result = await chat_task
            await tick_task
            return result

        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback") as mock_generate,
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[{"name": "hardware__action"}]),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability", return_value={"runtime_ok": True, "local_gateway_online": True}),
            patch("server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat", side_effect=blocking_stream) as mock_stream,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            result = _run(run_scenario())

        self.assertEqual(result["message"], "done")
        self.assertFalse(mock_generate.called)
        self.assertTrue(mock_stream.called)
        marks = {name: timestamp for name, timestamp in timeline}
        self.assertLess(marks["stream_start"], marks["tick"])
        self.assertLess(marks["tick"], marks["stream_end"])

    def test_main_sage_chat_executes_web_fetch_tool_for_url_fetch(self):
        stream_events = [
            self._trace(
                "tool.started",
                tool_call_id="call-fetch-1",
                data={"tool_name": "web__fetch", "args_preview": {"url": "https://example.com/docs"}},
            ),
            self._trace(
                "tool.result",
                tool_call_id="call-fetch-1",
                data={"status": "ok", "summary": "Fetched page text."},
            ),
            {"type": "final", "payload": {"reply": "Fetched page text.", "actions": [], "error": ""}},
        ]
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback") as mock_generate,
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability", return_value={"runtime_ok": False}),
            patch("server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat", return_value=iter(stream_events)) as mock_stream,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="fetch https://example.com/docs and summarize it",
            ))

        self.assertEqual(result["action_execution_mode"], "tools_executed")
        self.assertEqual(result["tool_calls"][0]["name"], "web__fetch")
        self.assertEqual(result["tool_calls"][0]["arguments"]["url"], "https://example.com/docs")
        self.assertEqual(result["message"], "Fetched page text.")
        self.assertFalse(mock_generate.called)
        self.assertTrue(mock_stream.called)

    def test_main_sage_chat_runs_morning_brief_daily_operator_recipe(self):
        capabilities = [
            {
                "id": "google_workspace",
                "label": "Google Workspace",
                "connected": True,
                "authenticated": True,
                "runtime_usable": True,
                "read_actions": ["gmail_threads.read", "calendar_events.read"],
                "write_actions": ["fetch_emails", "list_calendar_events"],
                "approval_required_actions": [],
            }
        ]

        def _execute(**kwargs):
            name = kwargs["tool_call"]["name"]
            if name == "google_workspace__fetch_emails":
                return 'Connector action completed: google_workspace.fetch_emails.\nResult: [{"subject":"Launch","snippet":"Ready."}]'
            if name == "google_workspace__list_calendar_events":
                return 'Connector action completed: google_workspace.list_calendar_events.\nResult: [{"summary":"Standup","start":"2026-06-07T09:00:00Z"}]'
            raise AssertionError(name)

        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback") as mock_generate,
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=capabilities),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability", return_value={"runtime_ok": False}),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._execute_single_direct_tool_call", side_effect=_execute) as mock_execute,
            patch("server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat") as mock_stream,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
            patch("server_modules.sage_agent_runtime_service.sage_proof_log_service.append_proof_log", return_value={"proof_id": "proof-morning"}) as mock_proof,
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="Create my morning brief from connected email and calendar.",
            ))

        self.assertEqual(result["action_execution_mode"], "daily_operator_executed")
        self.assertEqual(result["action_loop_version"], "daily_operator_v1")
        self.assertEqual(result["daily_operator"]["recipe_id"], "morning_brief")
        self.assertEqual([call["name"] for call in result["tool_calls"]], [
            "google_workspace__fetch_emails",
            "google_workspace__list_calendar_events",
        ])
        self.assertEqual(result["proof_log"]["version"], "daily_operator_proof_v1")
        self.assertEqual(result["proof_log"]["recipe_id"], "morning_brief")
        self.assertEqual(result["proof_log"]["status"], "completed")
        self.assertEqual([item["tool"] for item in result["proof_log"]["checked"]], [
            "google_workspace__fetch_emails",
            "google_workspace__list_calendar_events",
        ])
        self.assertEqual(result["proof_log"]["changes"], [])
        self.assertEqual(result["proof_log"]["blocked"], [])
        self.assertEqual(result["proof_log_id"], "proof-morning")
        self.assertEqual(result["proof_log"]["proof_id"], "proof-morning")
        self.assertEqual(mock_proof.call_args.kwargs["source"], "sage_chat")
        self.assertIn("Proof log", result["message"])
        self.assertFalse(mock_generate.called)
        self.assertFalse(mock_stream.called)
        self.assertEqual(mock_execute.call_count, 2)

    def test_main_sage_chat_blocks_meeting_prep_when_required_daily_operator_lanes_missing(self):
        capabilities = [
            {
                "id": "google_workspace",
                "label": "Google Workspace",
                "connected": True,
                "authenticated": True,
                "runtime_usable": True,
                "read_actions": ["gmail_threads.read"],
                "write_actions": ["fetch_emails"],
                "approval_required_actions": [],
            }
        ]
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback") as mock_generate,
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=capabilities),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability", return_value={"runtime_ok": False}),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._execute_single_direct_tool_call") as mock_execute,
            patch("server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat") as mock_stream,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
            patch("server_modules.sage_agent_runtime_service.sage_proof_log_service.append_proof_log", return_value={"proof_id": "proof-meeting"}) as mock_proof,
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="Prepare me for my next meeting using calendar and Drive.",
            ))

        self.assertEqual(result["action_execution_mode"], "daily_operator_blocked")
        self.assertEqual(result["daily_operator"]["recipe_id"], "meeting_prep")
        blocked_names = {item["name"] for item in result["blocked_tools"]}
        self.assertIn("google_workspace__list_calendar_events", blocked_names)
        self.assertIn("google_workspace__list_drive_files", blocked_names)
        self.assertEqual(result["proof_log"]["status"], "blocked")
        proof_blocked_names = {item["tool"] for item in result["proof_log"]["blocked"]}
        self.assertIn("google_workspace__list_calendar_events", proof_blocked_names)
        self.assertIn("google_workspace__list_drive_files", proof_blocked_names)
        self.assertEqual(result["proof_log"]["checked"], [])
        self.assertEqual(result["proof_log_id"], "proof-meeting")
        self.assertEqual(mock_proof.call_args.kwargs["status"], "blocked")
        self.assertFalse(mock_execute.called)
        self.assertFalse(mock_generate.called)
        self.assertFalse(mock_stream.called)

    def test_main_sage_chat_email_triage_reads_email_then_requests_draft_approval(self):
        capabilities = [
            {
                "id": "google_workspace",
                "label": "Google Workspace",
                "connected": True,
                "authenticated": True,
                "runtime_usable": True,
                "read_actions": ["gmail_threads.read"],
                "write_actions": ["fetch_emails", "draft_email"],
                "approval_required_actions": ["draft_email"],
            }
        ]

        def _execute(**kwargs):
            self.assertEqual(kwargs["tool_call"]["name"], "google_workspace__fetch_emails")
            return 'Connector action completed: google_workspace.fetch_emails.\nResult: [{"subject":"Customer","snippet":"Needs reply."}]'

        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback") as mock_generate,
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=capabilities),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability", return_value={"runtime_ok": False}),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._execute_single_direct_tool_call", side_effect=_execute) as mock_execute,
            patch("server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat") as mock_stream,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
            patch("server_modules.sage_agent_runtime_service.sage_proof_log_service.append_proof_log", return_value={"proof_id": "proof-email"}) as mock_proof,
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="Triage my important email and draft responses for approval.",
            ))

        self.assertEqual(result["action_execution_mode"], "approval_required")
        self.assertEqual(result["daily_operator"]["recipe_id"], "email_triage")
        self.assertEqual(result["tool_calls"][0]["name"], "google_workspace__fetch_emails")
        self.assertEqual(result["tool_calls"][0]["status"], "completed")
        self.assertEqual(result["tool_calls"][1]["name"], "google_workspace__draft_email")
        self.assertEqual(result["tool_calls"][1]["status"], "approval_required")
        self.assertEqual(result["approvals_required"][0]["actions"], ["draft_email"])
        self.assertEqual(result["proof_log"]["status"], "approval_required")
        self.assertEqual(result["proof_log"]["changes"], [])
        self.assertEqual(result["proof_log"]["checked"][0]["tool"], "google_workspace__fetch_emails")
        self.assertEqual(result["proof_log"]["approvals"][0]["actions"], ["draft_email"])
        self.assertEqual(result["proof_log"]["approvals"][0]["status"], "waiting")
        self.assertEqual(result["proof_log_id"], "proof-email")
        self.assertEqual(mock_proof.call_args.kwargs["status"], "approval_required")
        self.assertFalse(mock_generate.called)
        self.assertFalse(mock_stream.called)
        self.assertEqual(mock_execute.call_count, 1)

    def test_main_sage_chat_blocks_browser_when_agent_computer_offline(self):
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback") as mock_generate,
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability", return_value={"runtime_ok": False, "local_gateway_online": False}),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._execute_single_direct_tool_call") as mock_execute,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="open https://example.com in the browser",
            ))

        # Wave 1: browser-unavailable no longer hard-blocks with a canned
        # message. The general availability gate still fires (runtime_ok=False),
        # but the SPECIFIC tool name is no longer hardcoded to "browser__navigate"
        # — Sage receives context and can respond naturally when the runtime is
        # available. In this test (runtime offline), the availability gate still
        # produces tool_blocked, but NOT with the old browser-specific name.
        self.assertEqual(result["action_execution_mode"], "tool_blocked")
        blocked_names = [t.get("name") for t in (result.get("blocked_tools") or [])]
        self.assertNotIn(
            "browser__navigate", blocked_names,
            "Wave 1: browser-unavailable should not produce a hardcoded browser__navigate block",
        )
        self.assertFalse(mock_execute.called)

    def test_main_sage_chat_requests_approval_for_unsafe_shell_tool(self):
        stream_events = [
            {
                "type": "final",
                "payload": {
                    "reply": "",
                    "actions": [
                        {
                            "type": "approval_required",
                            "kind": "approval_required",
                            "connector": "shell",
                            "action": "exec",
                            "input": '{"command":"rm -rf /tmp/sage-action-loop-test"}',
                        }
                    ],
                    "approvals": [
                        {
                            "prompt": "Approve Shell to exec before continuing.",
                            "labels": ["shell.exec"],
                            "capabilities": ["shell"],
                            "actions": ["exec"],
                            "status": "waiting",
                        }
                    ],
                    "error": "",
                },
            }
        ]
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback") as mock_generate,
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability", return_value={"runtime_ok": True, "local_gateway_online": True}),
            patch("server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat", return_value=iter(stream_events)) as mock_stream,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="run command: rm -rf /tmp/sage-action-loop-test",
            ))

        # With internalized governance, approval is logged for audit but never blocks.
        # action_execution_mode is never "approval_required" — tools execute directly.
        self.assertNotEqual(result["action_execution_mode"], "approval_required",
                            f"Internalized governance: approval never blocks execution, got {result.get('action_execution_mode')}")
        self.assertEqual(result["tool_calls"][0]["name"], "shell__exec")
        # Status is "completed" — approval logged for audit, not blocking
        self.assertEqual(result["tool_calls"][0]["status"], "completed")
        self.assertGreater(len(result["approvals_required"]), 0)
        # generate is still called because approvals don't block — the LLM
        # responds naturally instead of emitting a blocking approval card
        self.assertTrue(mock_stream.called)

    def test_main_sage_chat_explains_blocked_tools_instead_of_silence(self):
        """2026-07-09 first-run integrity fix: a turn that ends with nothing
        substantive to say, with a tool blocked by policy along the way,
        must say so honestly instead of returning silence or an unrelated
        generic message."""
        stream_events = [
            {
                "type": "trace",
                "payload": {
                    "event_type": "trace.failed",
                    "tool_call_id": "call-1",
                    "data": {"code": "web__search", "message": "tool not enabled for this agent"},
                },
            },
            {
                "type": "final",
                "payload": {"reply": "", "actions": [], "error": ""},
            },
        ]
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback") as mock_generate,
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability", return_value={"runtime_ok": True, "local_gateway_online": True}),
            patch("server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat", return_value=iter(stream_events)),
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="search the web for today's news",
            ))

        self.assertTrue(result["blocked_tools"])
        self.assertNotEqual(result["message"], "")
        self.assertIn("doesn't have every tool turned on", result["message"])
        self.assertIn("Tools", result["message"])
        self.assertFalse(mock_generate.called)

    def test_main_sage_chat_invokes_matching_mcp_skill(self):
        mcp_skill = SimpleNamespace(
            id="mcp:inventory-feed:lookup_stock",
            label="Inventory Lookup",
            description="Lookup stock",
            action_class="read",
            requires_approval=False,
            execution_mode="live",
            enabled=True,
            available=True,
            execution_adapter="mcp_tool",
            trigger_terms=("inventory", "stock"),
            connector_scopes=("mcp", "mcp:inventory-feed"),
        )
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[mcp_skill]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback") as mock_generate,
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability", return_value={"runtime_ok": False}),
            patch("server_modules.sage_agent_runtime_service.skill_registry.execute_skill", new=AsyncMock(return_value={"status": "ok", "reply": "Inventory says 12 units."})) as mock_skill,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="use the inventory MCP tool to check stock",
            ))

        self.assertEqual(result["action_execution_mode"], "tools_executed")
        self.assertEqual(result["tool_calls"][0]["name"], "mcp:inventory-feed:lookup_stock")
        self.assertEqual(result["message"], "Inventory says 12 units.")
        self.assertFalse(mock_generate.called)
        self.assertTrue(mock_skill.called)

    def test_main_sage_chat_reports_operator_loop_budget_exhaustion(self):
        stream_events = [
            self._trace(
                "trace.failed",
                data={
                    "code": "max_tool_iterations_reached:6",
                    "message": "The Sage operator loop hit its iteration budget.",
                },
            ),
            {
                "type": "final",
                "payload": {
                    "reply": "",
                    "actions": [],
                    "error": "max_tool_iterations_reached:6",
                },
            },
        ]
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback") as mock_generate,
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability", return_value={"runtime_ok": False}),
            patch("server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat", return_value=iter(stream_events)) as mock_stream,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="search the web for a lot of things",
            ))

        self.assertEqual(result["action_execution_mode"], "tool_blocked")
        self.assertEqual(result["loop_budget"]["max_iterations"], sage_agent_runtime_service._SAGE_OPERATOR_LOOP_MAX_ITERATIONS)
        self.assertTrue(any(item["name"] == "max_tool_iterations_reached:6" for item in result["blocked_tools"]))
        self.assertFalse(mock_generate.called)
        self.assertTrue(mock_stream.called)

    def test_raises_on_provider_error(self):
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
            mock_provider.return_value = ("openai", {"api_key": "test-key"})
            mock_generate.return_value = ("", {}, "", "All providers failed")

            with self.assertRaises(RuntimeError):
                _run(sage_agent_runtime_service.handle_sage_chat(
                    workspace_id="ws-1", message="hi",
                ))

    def test_failed_turn_emits_failed_audit_with_trace_id(self):
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile") as mock_profile,
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files") as mock_files,
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block") as mock_mem,
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider") as mock_provider,
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback", side_effect=RuntimeError("provider crashed")),
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event") as mock_audit,
        ):
            mock_profile.return_value = {"profile": {"user_name": "", "identity_summary": "", "communication_style": "", "recurring_responsibility": "", "standing_rules": []}}
            mock_files.return_value = {}
            mock_mem.return_value = ""
            mock_provider.return_value = ("openai", {"api_key": "test-key"})

            with self.assertRaises(RuntimeError):
                _run(
                    sage_agent_runtime_service.handle_sage_chat(
                        workspace_id="ws-1",
                        tenant_id="t-1",
                        message="hi",
                        current_user={"user_id": "u-1"},
                    )
                )

            failed_calls = [c for c in mock_audit.call_args_list if c.kwargs.get("action") == "sage_chat.failed"]
            self.assertEqual(len(failed_calls), 1)
            self.assertEqual(failed_calls[0].kwargs.get("status"), "failed")
            self.assertTrue(str(failed_calls[0].kwargs.get("trace_id") or "").strip())


class SageTaskRouteDecisionTests(unittest.TestCase):
    def test_chat_only_route_for_plain_chat(self):
        decision = sage_agent_runtime_service._build_sage_route_decision(
            message="Write a short plan for tomorrow.",
        )

        self.assertEqual(decision["mode"], "chat_only")
        self.assertEqual(decision["user_label"], "Basic Assistant")
        self.assertEqual(decision["required_connections"], [])

    def test_connector_route_for_gmail_request(self):
        decision = sage_agent_runtime_service._build_sage_route_decision(
            message="Summarize my Gmail inbox.",
            tools=[{"name": "google_workspace__gmail_search"}],
            tool_capabilities=[{"id": "google_workspace", "label": "Google Workspace"}],
        )

        self.assertEqual(decision["mode"], "connector_api")
        self.assertEqual(decision["user_label"], "Connected Assistant")
        self.assertIn("gmail", decision["required_connections"])

    def test_connector_requests_enter_sage_action_loop(self):
        self.assertTrue(
            sage_agent_runtime_service._message_might_need_sage_action_loop(
                "Check my Google Calendar and create a meeting prep note."
            )
        )

    def test_run_colon_requests_enter_sage_action_loop(self):
        self.assertTrue(
            sage_agent_runtime_service._message_might_need_sage_action_loop(
                "run: echo hello from hardware"
            )
        )

    def test_local_hardware_requests_enter_sage_action_loop(self):
        self.assertTrue(
            sage_agent_runtime_service._message_might_need_sage_action_loop(
                "check what hardware I have on my Mac"
            )
        )

    def test_hardware_check_followup_routes_to_action_loop_with_context(self):
        prior_messages = [
            {"role": "assistant", "content": "Want me to run a quick hardware check or take a look at something specific?"}
        ]

        self.assertTrue(
            sage_agent_runtime_service._message_might_need_sage_action_loop(
                "ok check waht things i have !",
                prior_messages=prior_messages,
            )
        )
        self.assertEqual(
            sage_agent_runtime_service._normalized_sage_action_loop_message(
                "ok check waht things i have !",
                prior_messages,
            ),
            "check what hardware I have on my Mac",
        )

    def test_cloud_browser_route_for_website_automation(self):
        decision = sage_agent_runtime_service._build_sage_route_decision(
            message="Open example.com and fill the contact form.",
            availability={"runtime_ok": False, "local_gateway_online": False},
        )

        self.assertEqual(decision["mode"], "cloud_browser")
        self.assertEqual(decision["user_label"], "Connected Assistant")

    def test_cloud_computer_route_for_non_local_script(self):
        decision = sage_agent_runtime_service._build_sage_route_decision(
            message="Run this script and tell me the output.",
        )

        self.assertEqual(decision["mode"], "cloud_computer")
        self.assertEqual(decision["user_label"], "Computer Assistant")

    def test_gateway_route_for_local_private_work(self):
        decision = sage_agent_runtime_service._build_sage_route_decision(
            message="Open my local VS Code project and inspect the files.",
        )

        self.assertEqual(decision["mode"], "gateway_required")
        self.assertEqual(decision["user_label"], "Computer Assistant")
        self.assertTrue(decision["approval_required"])

    def test_gateway_route_for_local_system_overview(self):
        decision = sage_agent_runtime_service._build_sage_route_decision(
            message="check what hardware I have on my Mac",
        )

        self.assertEqual(decision["mode"], "gateway_required")
        self.assertEqual(decision["user_label"], "Computer Assistant")
        self.assertTrue(decision["approval_required"])


class SageActionLoopKillSwitchTests(unittest.TestCase):
    """The owner stop control's hard block — checked before any LLM call or
    tool bundling, first thing in _run_sage_action_loop_v3."""

    def _call(self, *, agent_install_id=""):
        return _run(sage_agent_runtime_service._run_sage_action_loop_v3(
            workspace_id="ws-1",
            tenant_id="tenant-1",
            message="hello",
            provider="anthropic",
            model="claude",
            credentials={},
            trace_id="trace-1",
            actor_user_id="user-1",
            system_prompt="",
            prior_messages=[],
            agent_install_id=agent_install_id,
        ))

    def test_agent_stopped_refuses_before_any_work(self):
        from server_modules import kill_switch_gate

        decision = kill_switch_gate.KillSwitchDecision(
            blocked=True, reason="agent_kill_active", scope="agent",
            detail="Agent agent-x has been stopped by its owner.",
        )
        with patch(
            "server_modules.kill_switch_gate.evaluate_kill_switch", return_value=decision,
        ) as evaluate_mock:
            result = self._call(agent_install_id="agent-x")

        evaluate_mock.assert_called_once_with(tenant_id="tenant-1", workspace_id="ws-1", agent_id="agent-x")
        self.assertIsNotNone(result)
        self.assertIn("stopped", result["message"].lower())
        self.assertIn("owner", result["message"].lower())
        self.assertEqual(result["error"], "agent_kill_active")
        self.assertEqual(result["action_execution_mode"], "blocked")
        self.assertEqual(result["tool_calls"], [])
        self.assertEqual(result["available_tools"], [])

    def test_workspace_stopped_refuses_every_agent(self):
        from server_modules import kill_switch_gate

        decision = kill_switch_gate.KillSwitchDecision(
            blocked=True, reason="workspace_kill_active", scope="workspace",
            detail="Workspace ws-1 has been stopped.",
        )
        with patch("server_modules.kill_switch_gate.evaluate_kill_switch", return_value=decision):
            result = self._call(agent_install_id="agent-x")

        self.assertIn("all agents", result["message"].lower())
        self.assertIn("workspace", result["message"].lower())

    def test_workspace_stop_also_refuses_sage_itself(self):
        """agent_install_id="" (Sage/master, not a specialist) still hits the
        workspace-wide block — 'stop all agents' means all agents, including
        the operator's own conversation."""
        from server_modules import kill_switch_gate

        decision = kill_switch_gate.KillSwitchDecision(
            blocked=True, reason="workspace_kill_active", scope="workspace",
            detail="Workspace ws-1 has been stopped.",
        )
        with patch("server_modules.kill_switch_gate.evaluate_kill_switch", return_value=decision):
            result = self._call(agent_install_id="")

        self.assertIsNotNone(result)
        self.assertEqual(result["action_execution_mode"], "blocked")

    def test_not_stopped_reaches_real_dispatch(self):
        """A false decision must NOT trip the early return — proceeds past
        the block into the real loop body. Patches the very next thing the
        function touches (_resolve_specialist_toolset) to prove control
        reached there, rather than asserting on an unmocked downstream
        failure."""
        from server_modules import kill_switch_gate

        decision = kill_switch_gate.KillSwitchDecision(blocked=False, reason="", scope="")
        with (
            patch("server_modules.kill_switch_gate.evaluate_kill_switch", return_value=decision),
            patch.object(
                sage_agent_runtime_service, "_resolve_specialist_toolset",
                new=AsyncMock(side_effect=RuntimeError("reached_specialist_toolset_resolution")),
            ),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self._call(agent_install_id="agent-x")
        self.assertEqual(str(ctx.exception), "reached_specialist_toolset_resolution")


if __name__ == "__main__":
    unittest.main()

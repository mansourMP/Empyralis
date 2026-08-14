from __future__ import annotations

import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from server_modules import compaction_service
from server_modules import sage_agent_runtime_service
from server_modules.specialist_runtime_context import SpecialistRuntimeContext
from server_modules.tests.support_live_llm_stubs import patched_provider_calls


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
            patched_provider_calls(reply="Reply", usage={"model": "d"}, provider="deepseek"),
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
            patched_provider_calls(reply="Reply", provider="deepseek"),
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
            patched_provider_calls(reply="Reply", provider="deepseek"),
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
            patched_provider_calls(reply="Reply", provider="deepseek"),
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
            patched_provider_calls(reply="Reply", provider="deepseek"),
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


class SageAgentRuntimeAttachmentContextTests(unittest.TestCase):
    """Regression coverage for the crash where _load_attachment_context
    treated every attachment as a dict and called .get() on it, while the
    live web-chat path (direct_chat_service.execute_direct_chat_turn_request,
    which routes through handle_sage_chat) fed it AgentTurnRequest.attachments
    -- TurnAttachment dataclass instances -- straight through. Every
    attachment upload raised AttributeError, 100% of the time.

    agent_turn.py's sage_chat_attachment_dict(s)_from_turn_attachment is now
    the one conversion boundary between the two type contracts (see its own
    coverage in test_agent_turn.py). _load_attachment_context's contract
    (list[dict] | None) is unchanged -- these tests exercise it directly
    with the canonical dict shape every caller must now provide.
    """

    def test_reads_text_attachment_content_from_the_canonical_dict_shape(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            attachments_dir = Path(tmp_dir)
            (attachments_dir / "safe-notes.txt").write_text("hello from disk", encoding="utf-8")

            with patch(
                "server_modules.sage_agent_runtime_service.workspace_context.workspace_attachments_dir",
                return_value=attachments_dir,
            ):
                context = _run(sage_agent_runtime_service._load_attachment_context(
                    workspace_id="ws-1",
                    attachments=[
                        {
                            "file_id": "file-1",
                            "filename": "notes.txt",
                            "safe_filename": "safe-notes.txt",
                            "content_type": "text/plain",
                            "size": 16,
                            "url": "https://files.example.com/safe-notes.txt",
                        }
                    ],
                ))

        self.assertIn("## Attached Files", context)
        self.assertIn("notes.txt", context)
        self.assertIn("hello from disk", context)

    def test_skips_attachment_missing_from_disk_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch(
                "server_modules.sage_agent_runtime_service.workspace_context.workspace_attachments_dir",
                return_value=Path(tmp_dir),
            ):
                context = _run(sage_agent_runtime_service._load_attachment_context(
                    workspace_id="ws-1",
                    attachments=[
                        {
                            "filename": "ghost.txt",
                            "safe_filename": "missing-on-disk.txt",
                            "content_type": "text/plain",
                        }
                    ],
                ))

        self.assertNotIn("ghost.txt", context)

    def test_empty_attachments_returns_empty_string(self):
        context = _run(sage_agent_runtime_service._load_attachment_context(
            workspace_id="ws-1",
            attachments=None,
        ))
        self.assertEqual(context, "")

    def test_raises_on_a_bare_turn_attachment_instead_of_the_canonical_dict(self):
        # Documents the contract: _load_attachment_context is NOT dual-shape
        # tolerant -- the conversion belongs at the caller boundary
        # (sage_chat_attachment_dict_from_turn_attachment), never here. A
        # bare TurnAttachment reaching this function is exactly the bug that
        # crashed every attachment upload; this guards against silently
        # "fixing" it with a defensive isinstance branch that would let both
        # shapes keep flowing through the codebase.
        from server_modules.agent_turn import TurnAttachment

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch(
                "server_modules.sage_agent_runtime_service.workspace_context.workspace_attachments_dir",
                return_value=Path(tmp_dir),
            ):
                with self.assertRaises(AttributeError):
                    _run(sage_agent_runtime_service._load_attachment_context(
                        workspace_id="ws-1",
                        attachments=[TurnAttachment(kind="file", uri="https://x/y.txt", name="y.txt")],
                    ))


class SageAgentRuntimeSafetyTests(unittest.TestCase):
    def _setup_mocks(self, *, profile_overrides=None, skills=None, memory_return="", files_return=None, reply="Reply"):
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
        mocks["generate"] = patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback", return_value=(reply, {"model": "d"}, "deepseek", ""))
        # mocks["generate"] above only covers the LEGACY fallback entry
        # points (sage_agent_runtime_service lines 4104 / 5885 / 6573). The
        # ordinary turn goes through the streaming seam instead -- see
        # support_live_llm_stubs -- so this is the one that actually keeps a
        # test off the network.
        mocks["provider_stream"] = patched_provider_calls(reply=reply, usage={"model": "d"}, provider="deepseek")
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
            mocks["generate"], mocks["provider_stream"], mocks["persist"], mocks["activity"], mocks["audit"],
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
            mocks["generate"] as mock_gen, mocks["provider_stream"] as sage_stream,
            mocks["persist"], mocks["activity"], mocks["audit"],
        ):
            _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="hello",
            ))

            # Read the prompt off the call the turn REALLY makes. mock_gen
            # (the non-streaming fallback seam) is not called on an ordinary
            # turn at all, so mock_gen.call_args was None-shaped and every
            # assertion below was unreachable while the turn itself went to
            # a live provider -- see support_live_llm_stubs.
            self.assertEqual(sage_stream.call_count, 1)
            system_prompt = sage_stream.system_prompt
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
        mocks = self._setup_mocks(reply=dsml_reply)
        mocks["generate"] = patch(
            "server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback",
            return_value=(dsml_reply, {"model": "deepseek-chat"}, "deepseek", ""),
        )
        with (
            mocks["profile"], mocks["files"], mocks["memory"], mocks["heartbeat"],
            mocks["skills"], mocks["provider"],
            mocks["generate"], mocks["provider_stream"], mocks["persist"] as mock_persist, mocks["activity"], mocks["audit"],
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
            mocks["generate"], mocks["provider_stream"], mocks["persist"], mocks["activity"],
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
            mocks["generate"], mocks["provider_stream"], mocks["persist"], mocks["activity"], mocks["audit"], mocks["approval"],
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
                    mocks["generate"], mocks["provider_stream"], mocks["persist"], mocks["activity"], mocks["audit"],
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
            mocks["generate"], mocks["provider_stream"], mocks["persist"], mocks["activity"], mocks["audit"], mocks["approval"],
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
            mocks["generate"], mocks["provider_stream"], mocks["persist"], mocks["activity"], mocks["audit"], mocks["approval"],
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
            mocks["skills"], mocks["provider"], mocks["generate"], mocks["provider_stream"], mocks["persist"],
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
            patched_provider_calls(reply="Reply", provider="deepseek"),
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
            
                engine_options={"engine": "legacy"},
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
            
                engine_options={"engine": "legacy"},
            ))

            failed_calls = [
                call for call in mock_activity.await_args_list
                if call.kwargs.get("action") == "sage_chat.failed"
            ]
            self.assertEqual(len(failed_calls), 1)
            kwargs = failed_calls[0].kwargs
            self.assertEqual(kwargs["status"], "error")
            self.assertEqual(kwargs["title"], "Agent chat failed")
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
            patched_provider_calls(reply="Reply", provider="deepseek"),
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
            patched_provider_calls(reply="Reply", provider="deepseek"),
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
            patched_provider_calls(reply="Hello there", usage={"model": "gpt-4o"}, provider="openai"),
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
            
                engine_options={"engine": "legacy"},
            ))

        self.assertEqual(result["action_execution_mode"], "tools_executed")
        self.assertEqual(result["action_loop_version"], "v3")
        self.assertEqual(result["tool_calls"][0]["name"], "web__search")
        self.assertIn("OpenClaw browser docs", result["tool_calls"][0]["arguments"]["query"])
        self.assertEqual(result["message"], "I found the OpenClaw browser docs.")
        self.assertFalse(mock_generate.called)
        self.assertTrue(mock_stream.called)
        stream_kwargs = mock_stream.call_args.kwargs
        self.assertIn("You're the user's personal AI assistant", stream_kwargs["system_prompt"])
        self.assertEqual(stream_kwargs["session_ctx"]["agent_turn_request"]["policy_context"]["agent_scope"], "sage")
        self.assertEqual(stream_kwargs["session_ctx"]["agent_turn_request"]["policy_context"]["agent_id"], "sage_main_agent")

    def test_main_sage_chat_surfaces_media_queued_by_a_tool_call(self):
        """End-to-end plumbing proof: a tool call that appends to
        session_ctx["pending_outbound_media"] (exactly what
        skills_service.execute_single_direct_tool_call's send_image handler
        and generate_image's auto-attach do — see
        _run_sage_action_loop_v3's session_ctx construction) must surface as
        handle_sage_chat's "media" response key. The real tool executor
        isn't invoked here (stream_provider_backed_direct_chat is mocked, as
        in test_main_sage_chat_executes_web_search_tool above) — this test's
        side_effect stands in for it by mutating the SAME session_ctx object
        the mock receives, the same way the real executor does via the
        ThreadPoolExecutor hop in direct_chat_generation_service.py."""
        media_item = {"kind": "image", "source_path": "/tmp/fox.png", "mime_type": "image/png"}
        stream_events = [
            self._trace(
                "tool.started",
                tool_call_id="call-send-image-1",
                data={"tool_name": "send_image", "args_preview": {"path_or_url": "/tmp/fox.png"}},
            ),
            self._trace(
                "tool.result",
                tool_call_id="call-send-image-1",
                data={"status": "ok", "summary": "Queued image to send: /tmp/fox.png"},
            ),
            {"type": "final", "payload": {"reply": "Sent!", "actions": [], "error": ""}},
        ]

        def _fake_stream(*_args, **kwargs):
            session_ctx = kwargs.get("session_ctx")
            self.assertIsInstance(session_ctx, dict)
            self.assertEqual(session_ctx.get("pending_outbound_media"), [])
            session_ctx["pending_outbound_media"].append(media_item)
            return iter(stream_events)

        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback"),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability", return_value={"runtime_ok": False}),
            patch("server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat", side_effect=_fake_stream),
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="send me that fox picture",
                channel_origin="whatsapp_personal",
            
                engine_options={"engine": "legacy"},
            ))

        self.assertEqual(result["message"], "Sent!")
        self.assertEqual(result["media"], [media_item])

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
            
                engine_options={"engine": "legacy"},
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
            
                engine_options={"engine": "legacy"},
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
            
                engine_options={"engine": "legacy"},
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
            
                engine_options={"engine": "legacy"},
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
            
                engine_options={"engine": "legacy"},
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
            patched_provider_calls(reply="Reply", provider="openai"),
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
            
                engine_options={"engine": "legacy"},
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

    def test_main_sage_chat_no_reply_with_provider_failure_is_honest_not_tools_blamed(self):
        """2026-08-14 correction of the 2026-07-09 first-run integrity fix.

        The original fix (git blame this test's prior name,
        test_main_sage_chat_explains_blocked_tools_instead_of_silence)
        treated ANY non-empty blocked_tools as proof a tool was disabled by
        policy. Traced end to end after a founder-reported production bug
        (DeepSeek/Flash tier, an ordinary chat message, no tool need at all,
        got told "This agent doesn't have every tool turned on"): every real
        blocked_tools producer in this codebase — claude_agent_sdk_bridge.py
        (the production-default engine)'s provider-error/foreign-tool/
        orphan-tool-result trace.failed events, AND direct_chat_generation_
        service.py's own cost-ceiling/tool-loop-detected/generation-error
        trace.failed events — represents a FAILURE, never "a tool is not
        enabled". This test's OWN prior fixture (`code: "web__search",
        message: "tool not enabled for this agent"`) never matched any real
        producer either — it was an invented shape, exactly the "a fixture
        that invents its own input cannot notice the real input is shaped
        differently" failure mode this codebase has hit before. Replaced
        with the REAL shape claude_agent_sdk_bridge.py emits for a genuine
        provider failure (code="provider_generation_failed"), and the
        assertion is now that the customer is told the honest thing: a
        reply did not come back and the cause is not known from here — not
        a fabricated diagnosis blaming tool settings.
        """
        stream_events = [
            {
                "type": "trace",
                "payload": {
                    "event_type": "trace.failed",
                    "tool_call_id": "call-1",
                    "data": {"code": "provider_generation_failed", "message": "The model provider returned an error (server_error)."},
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

                engine_options={"engine": "legacy"},
            ))

        self.assertTrue(result["blocked_tools"])
        self.assertNotEqual(result["message"], "")
        # The old (wrong) diagnosis must NOT appear — the cause was never
        # tool settings.
        self.assertNotIn("doesn't have every tool turned on", result["message"])
        # The new, honest message: no fabricated cause, a real next step.
        self.assertIn("cause is not known", result["message"])
        self.assertIn("Work tab", result["message"])
        self.assertFalse(mock_generate.called)

    def test_main_sage_chat_uses_tools_limited_copy_for_a_recognized_policy_code(self):
        """Forward-compatibility check for _classify_sage_no_reply_outcome:
        if a future producer starts emitting a blocked_tools entry whose
        code is a genuine, recognized tool-capability policy decision (the
        SAGE_BLOCKED_TOOLS_POLICY_CODES allowlist, now in
        server_modules/sage_blocked_tools_outcome.py — empty today because
        no live producer emits one, see that constant's own comment), the
        TOOLS_LIMITED_NO_REPLY copy is still reachable and still correct.
        Patches the allowlist directly rather than inventing a fake
        producer shape, so this test cannot silently pass against a
        fixture that no real code path produces (the exact mistake the
        sibling test above replaces)."""
        stream_events = [
            {
                "type": "trace",
                "payload": {
                    "event_type": "trace.failed",
                    "tool_call_id": "call-1",
                    "data": {"code": "agent_tool_capability_denied", "message": "not bound to this agent"},
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
            patch(
                "server_modules.sage_blocked_tools_outcome.SAGE_BLOCKED_TOOLS_POLICY_CODES",
                frozenset({"agent_tool_capability_denied"}),
            ),
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="search the web for today's news",

                engine_options={"engine": "legacy"},
            ))

        self.assertTrue(result["blocked_tools"])
        self.assertIn("doesn't have every tool turned on", result["message"])
        self.assertIn("Tools", result["message"])
        self.assertFalse(mock_generate.called)

    def test_main_sage_chat_invokes_matching_mcp_skill(self):
        """Phase A wiring (docs/design/mcp-applications-plan.md) removed the
        keyword-matched NL routing (_matching_mcp_skill + the dead
        _run_sage_action_loop_v2's skill_registry.execute_skill dispatch)
        this test used to exercise — that function had zero live callers
        (handle_sage_chat only ever calls _run_sage_action_loop_v3, which
        computed the same match and discarded it). MCP tools are now real,
        structurally-callable tools invoked by name
        (mcp__<server>__<tool>) through ordinary model tool-calling, not a
        keyword match against the raw user message. This test is adapted to
        simulate that: it mocks stream_provider_backed_direct_chat directly
        to emit the tool.started/tool.result/final trace events the real
        dispatch layer produces once a model-issued call to an
        mcp-namespaced tool completes (same pattern as
        server_modules/tests/test_sage_mcp_bridge_v1.py's
        TestApprovedMCPToolExecutes). See test_mcp_tool_calling_wiring.py
        for coverage of the actual dispatch-routing logic.
        """
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
        stream_events = [
            {
                "type": "trace",
                "payload": {
                    "event_type": "tool.started",
                    "tool_call_id": "call-1",
                    "data": {"tool_name": "mcp__inventory-feed__lookup_stock", "args_preview": {}},
                },
            },
            {
                "type": "trace",
                "payload": {
                    "event_type": "tool.result",
                    "tool_call_id": "call-1",
                    "data": {"status": "ok", "summary": "Inventory says 12 units."},
                },
            },
            {"type": "final", "payload": {"reply": "Inventory says 12 units.", "actions": [], "error": ""}},
        ]
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
            patch("server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat", return_value=iter(stream_events)),
            patch("server_modules.sage_agent_runtime_service.skill_registry.execute_skill", new=AsyncMock()) as mock_skill,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="use the inventory MCP tool to check stock",
            
                engine_options={"engine": "legacy"},
            ))

        self.assertEqual(result["action_execution_mode"], "tools_executed")
        self.assertEqual(result["tool_calls"][0]["name"], "mcp__inventory-feed__lookup_stock")
        self.assertEqual(result["message"], "Inventory says 12 units.")
        self.assertFalse(mock_generate.called)
        # The old keyword-matched skill_registry.execute_skill bridge must
        # never fire from the live v3 loop.
        self.assertFalse(mock_skill.called)

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
            
                engine_options={"engine": "legacy"},
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
            patched_provider_calls(reply="", error="All providers failed"),
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
            patched_provider_calls(raises=RuntimeError("provider crashed")),
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


class SanitizeAgentReplySendImageTests(unittest.TestCase):
    """send_image's leaked-tool-call sanitization — previously missing from
    _KNOWN_TOOL_PREFIXES, so a model that leaked raw
    'send_image(path_or_url="...")'-shaped text into its reply (instead of
    a real tool call) would ship that syntax straight to the user in the
    chat. generate_image was already covered before this fix; send_image
    was not."""

    def test_known_tool_prefixes_includes_send_image(self):
        self.assertIn("send_image", sage_agent_runtime_service._KNOWN_TOOL_PREFIXES)

    def test_sanitizes_leaked_parenthesized_call(self):
        leaked = 'Sure, one sec.\nsend_image(path_or_url="fox.png", caption="here")\nAll done!'
        cleaned = sage_agent_runtime_service.sanitize_agent_reply(leaked)
        self.assertNotIn("send_image(", cleaned)
        self.assertIn("Sure, one sec.", cleaned)
        self.assertIn("All done!", cleaned)

    def test_sanitizes_leaked_space_separated_call(self):
        leaked = 'Here you go.\nsend_image path_or_url="fox.png"\nEnjoy!'
        cleaned = sage_agent_runtime_service.sanitize_agent_reply(leaked)
        self.assertNotIn("send_image", cleaned)
        self.assertIn("Here you go.", cleaned)
        self.assertIn("Enjoy!", cleaned)

    def test_real_prose_mentioning_send_image_is_untouched(self):
        # Sanity check against over-eager stripping: a normal sentence that
        # happens to contain the word "image" must survive intact.
        clean_text = "I generated an image and sent it over — let me know if you want another."
        self.assertEqual(sage_agent_runtime_service.sanitize_agent_reply(clean_text), clean_text)


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


class SdkEngineSessionLookupTests(unittest.TestCase):
    """MAN-310 Phase 2: _sdk_engine_session_lookup is the fingerprint check
    that decides whether a previously captured claude_agent_sdk session id
    is still safe to resume for THIS turn — see its own docstring for the
    count-based staleness invariant. Exercised directly (not through the
    full action loop) so the matching/mismatching logic is pinned in
    isolation."""

    def _thread_row(self, metadata):
        return {"thread_id": "thread-1", "metadata": metadata}

    def test_no_thread_id_returns_empty(self):
        result = _run(sage_agent_runtime_service._sdk_engine_session_lookup(
            thread_id="", tenant_id="t-1", workspace_id="ws-1", prior_message_count=0,
        ))
        self.assertEqual(result, "")

    def test_matching_fingerprint_resumes(self):
        stored = self._thread_row({"claude_agent_sdk_session": {"session_id": "sess-1", "turn_fingerprint": 4}})
        with patch(
            "server_modules.sage_agent_runtime_service.thread_service.get_thread",
            new=AsyncMock(return_value=stored),
        ):
            result = _run(sage_agent_runtime_service._sdk_engine_session_lookup(
                thread_id="thread-1", tenant_id="t-1", workspace_id="ws-1", prior_message_count=4,
            ))
        self.assertEqual(result, "sess-1")

    def test_mismatched_fingerprint_refuses_to_resume(self):
        """A different prior_message_count than what the stored session was
        captured against means something else touched this thread in
        between (a legacy-engine turn, a channel-injected message, a
        background compaction summary) -- the session's own memory is
        stale relative to what this turn is about to see, so resuming
        would silently drop that history. Must return "", not the stale id."""
        stored = self._thread_row({"claude_agent_sdk_session": {"session_id": "sess-1", "turn_fingerprint": 4}})
        with patch(
            "server_modules.sage_agent_runtime_service.thread_service.get_thread",
            new=AsyncMock(return_value=stored),
        ):
            result = _run(sage_agent_runtime_service._sdk_engine_session_lookup(
                thread_id="thread-1", tenant_id="t-1", workspace_id="ws-1", prior_message_count=6,
            ))
        self.assertEqual(result, "")

    def test_no_stored_session_returns_empty(self):
        stored = self._thread_row({})
        with patch(
            "server_modules.sage_agent_runtime_service.thread_service.get_thread",
            new=AsyncMock(return_value=stored),
        ):
            result = _run(sage_agent_runtime_service._sdk_engine_session_lookup(
                thread_id="thread-1", tenant_id="t-1", workspace_id="ws-1", prior_message_count=0,
            ))
        self.assertEqual(result, "")

    def test_metadata_as_raw_json_string_is_decoded(self):
        """The real (Postgres) path returns agent_threads.metadata as a raw
        JSON string, not a dict (no jsonb codec registered on that pool) --
        must still be read correctly."""
        stored = self._thread_row(
            '{"claude_agent_sdk_session": {"session_id": "sess-json", "turn_fingerprint": 2}}'
        )
        with patch(
            "server_modules.sage_agent_runtime_service.thread_service.get_thread",
            new=AsyncMock(return_value=stored),
        ):
            result = _run(sage_agent_runtime_service._sdk_engine_session_lookup(
                thread_id="thread-1", tenant_id="t-1", workspace_id="ws-1", prior_message_count=2,
            ))
        self.assertEqual(result, "sess-json")

    def test_lookup_exception_fails_closed(self):
        with patch(
            "server_modules.sage_agent_runtime_service.thread_service.get_thread",
            new=AsyncMock(side_effect=RuntimeError("kernel unavailable")),
        ):
            result = _run(sage_agent_runtime_service._sdk_engine_session_lookup(
                thread_id="thread-1", tenant_id="t-1", workspace_id="ws-1", prior_message_count=0,
            ))
        self.assertEqual(result, "")


class SdkEngineSessionPersistTests(unittest.TestCase):
    """_sdk_engine_session_persist's write side -- best-effort, never raises,
    writes the exact metadata_patch shape _sdk_engine_session_lookup later
    reads back."""

    def test_writes_expected_metadata_patch(self):
        mock_merge = AsyncMock(return_value=None)
        with patch("server_modules.control_plane_repository.merge_agent_thread_metadata", new=mock_merge):
            _run(sage_agent_runtime_service._sdk_engine_session_persist(
                thread_id="thread-1", tenant_id="t-1", workspace_id="ws-1",
                session_id="sess-2", next_turn_fingerprint=6,
            ))
        mock_merge.assert_awaited_once_with(
            thread_id="thread-1",
            tenant_id="t-1",
            workspace_id="ws-1",
            metadata_patch={"claude_agent_sdk_session": {"session_id": "sess-2", "turn_fingerprint": 6}},
        )

    def test_empty_thread_id_is_a_noop(self):
        mock_merge = AsyncMock(return_value=None)
        with patch("server_modules.control_plane_repository.merge_agent_thread_metadata", new=mock_merge):
            _run(sage_agent_runtime_service._sdk_engine_session_persist(
                thread_id="", tenant_id="t-1", workspace_id="ws-1",
                session_id="sess-2", next_turn_fingerprint=6,
            ))
        mock_merge.assert_not_awaited()

    def test_empty_session_id_is_a_noop(self):
        mock_merge = AsyncMock(return_value=None)
        with patch("server_modules.control_plane_repository.merge_agent_thread_metadata", new=mock_merge):
            _run(sage_agent_runtime_service._sdk_engine_session_persist(
                thread_id="thread-1", tenant_id="t-1", workspace_id="ws-1",
                session_id="", next_turn_fingerprint=6,
            ))
        mock_merge.assert_not_awaited()

    def test_write_failure_does_not_raise(self):
        mock_merge = AsyncMock(side_effect=RuntimeError("kernel unavailable"))
        with patch("server_modules.control_plane_repository.merge_agent_thread_metadata", new=mock_merge):
            _run(sage_agent_runtime_service._sdk_engine_session_persist(
                thread_id="thread-1", tenant_id="t-1", workspace_id="ws-1",
                session_id="sess-2", next_turn_fingerprint=6,
            ))  # must not raise


class SdkEngineSessionContinuityIntegrationTests(unittest.TestCase):
    """MAN-310 Phase 2's central claim, exercised end to end through
    _run_sage_action_loop_v3 (not just the two helpers above in isolation):
    a SECOND SDK-engine turn on the SAME conversation resumes the session
    the FIRST turn minted, instead of re-sending full history. Session
    storage is faked with a plain dict standing in for the agent_threads
    row (thread_service.get_thread / control_plane_repository.merge_agent_
    thread_metadata are both patched to read/write it) so this runs with no
    real database and no compiled Rust kernel -- both unavailable in this
    test environment -- while still exercising the REAL _sdk_engine_
    session_lookup/_sdk_engine_session_persist logic in between."""

    def _decision(self):
        from server_modules import kill_switch_gate
        return kill_switch_gate.KillSwitchDecision(blocked=False, reason="", scope="")

    def _call(self, *, prior_messages, thread_row):
        get_thread_mock = AsyncMock(return_value={"metadata": dict(thread_row.get("metadata") or {})})

        async def _fake_merge(*, thread_id, tenant_id, workspace_id, metadata_patch):
            thread_row.setdefault("metadata", {}).update(metadata_patch)

        collect_mock = MagicMock(return_value=[
            {"type": "final", "payload": {"reply": "ok", "session_id": thread_row.pop("_next_session_id")}},
        ])
        with (
            patch("server_modules.kill_switch_gate.evaluate_kill_switch", return_value=self._decision()),
            patch("server_modules.sage_agent_runtime_service.thread_service.get_thread", new=get_thread_mock),
            patch(
                "server_modules.control_plane_repository.merge_agent_thread_metadata",
                new=AsyncMock(side_effect=_fake_merge),
            ),
            patch.object(
                sage_agent_runtime_service.claude_agent_sdk_bridge,
                "collect_events_via_claude_agent_sdk",
                new=collect_mock,
            ),
        ):
            _run(sage_agent_runtime_service._run_sage_action_loop_v3(
                workspace_id="ws-1", tenant_id="tenant-1", message="hello",
                provider="anthropic", model="claude", credentials={},
                trace_id="trace-1", actor_user_id="user-1", system_prompt="",
                prior_messages=prior_messages, agent_install_id="",
                engine_options={"engine": "claude_agent_sdk"},
                conversation_thread_id="thread-xyz",
            ))
        return collect_mock

    def test_first_turn_has_no_session_to_resume(self):
        thread_row = {"metadata": {}, "_next_session_id": "sess-1"}
        collect_mock = self._call(prior_messages=[], thread_row=thread_row)
        _, kwargs = collect_mock.call_args
        self.assertEqual(kwargs["resume_session_id"], "")
        # ...and persists what it minted, fingerprinted for the NEXT turn
        # (0 prior + this turn's own user+assistant pair = 2).
        self.assertEqual(
            thread_row["metadata"]["claude_agent_sdk_session"],
            {"session_id": "sess-1", "turn_fingerprint": 2},
        )

    def test_second_turn_resumes_the_first_turns_session(self):
        """The proof the report asks for: turn 2, on the same conversation,
        with prior_messages matching what turn 1 left behind, must be
        called with resume_session_id set to turn 1's session id -- NOT
        re-sent full history via a folded prompt (that decision lives in
        claude_agent_sdk_bridge.run_claude_agent_sdk_turn, proven separately
        in test_claude_agent_sdk_bridge.py; this test proves the ORCHESTRATION
        layer feeds it the right resume token in the first place)."""
        thread_row = {"metadata": {}, "_next_session_id": "sess-1"}
        self._call(prior_messages=[], thread_row=thread_row)
        self.assertEqual(
            thread_row["metadata"]["claude_agent_sdk_session"]["session_id"], "sess-1",
        )

        # Turn 2: Empyralis's own thread store now shows the 2 turns turn 1
        # just recorded (user message + assistant reply) -- exactly the
        # fingerprint turn 1 persisted.
        turn_2_prior_messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "ok"},
        ]
        thread_row["_next_session_id"] = "sess-2"
        collect_mock = self._call(prior_messages=turn_2_prior_messages, thread_row=thread_row)
        _, kwargs = collect_mock.call_args
        self.assertEqual(kwargs["resume_session_id"], "sess-1")
        # Session id rolled forward for turn 3, fingerprint advanced by 2 again.
        self.assertEqual(
            thread_row["metadata"]["claude_agent_sdk_session"],
            {"session_id": "sess-2", "turn_fingerprint": 4},
        )

    def test_drifted_history_refuses_to_resume(self):
        """If something else appended to the thread between turn 1 and turn
        2 (e.g. a legacy-engine turn, a channel message), turn 2's real
        prior_messages length no longer matches turn 1's fingerprint --
        must NOT resume."""
        thread_row = {"metadata": {}, "_next_session_id": "sess-1"}
        self._call(prior_messages=[], thread_row=thread_row)

        turn_2_prior_messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "an interleaved message this session never saw"},
        ]
        thread_row["_next_session_id"] = "sess-2"
        collect_mock = self._call(prior_messages=turn_2_prior_messages, thread_row=thread_row)
        _, kwargs = collect_mock.call_args
        self.assertEqual(kwargs["resume_session_id"], "")


class CliSubscriptionGatewayBrainTests(unittest.TestCase):
    """BYO-brain Phase 3: cli_subscription dispatch — the happy path plus
    every G5 error path (no gateway bound, gateway offline, CLI not
    installed, CLI not authenticated, timeout, crash). Mirrors
    _dispatch_local_gateway_brain's shape but adds the runtime-specific
    (claude_code vs codex) readiness pre-check "local"/ollama never needed."""

    @staticmethod
    def _registration(**overrides):
        base = {
            "gateway_id": "gateway-1",
            "workspace_id": "ws-1",
            "status": "active",
            "device_trust_state": "trusted",
        }
        base.update(overrides)
        return base

    @staticmethod
    def _llm_runtimes_payload(*, claude_code=True, codex=True):
        return {
            "llm_runtimes": {
                "claude_code": {"installed": claude_code, "authenticated": claude_code},
                "codex": {"installed": codex, "authenticated": codex},
            }
        }

    def test_happy_path_returns_reply_usage_and_model(self):
        response = {
            "result": {"text": "hello from claude", "model": "claude-sonnet-4-6", "usage": {"input_tokens": 12, "output_tokens": 4}},
        }
        with (
            patch("server_modules.gateway_state_repository.get_gateway_registration", return_value=self._registration()),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value=self._llm_runtimes_payload(),
            ),
            patch("server_modules.gateway_execution_service.execute_tool_via_gateway", new=AsyncMock(return_value=response)),
            patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock()) as mock_ledger,
            patch("server_modules.usage_events_repository.record_usage_event", new=AsyncMock()) as mock_usage,
        ):
            reply, usage, model = _run(
                sage_agent_runtime_service._dispatch_cli_subscription_gateway_brain(
                    workspace_id="ws-1", tenant_id="t-1", agent_id="agent-1",
                    gateway_binding="gateway-1", runtime="claude_code", model="claude-sonnet-4-6",
                    system_prompt="Be terse.", user_message="hi",
                )
            )
        self.assertEqual(reply, "hello from claude")
        self.assertEqual(usage, {"input_tokens": 12, "output_tokens": 4})
        self.assertEqual(model, "claude-sonnet-4-6")
        # Ledgered as a completed turn, tagged gateway_brain, never blocked.
        completed_calls = [c for c in mock_ledger.await_args_list if c.kwargs.get("action") == "gateway_brain_turn"]
        self.assertEqual(len(completed_calls), 1)
        self.assertEqual(completed_calls[0].kwargs["status"], "completed")
        self.assertEqual(completed_calls[0].kwargs["metadata"]["execution_tier"], "gateway_brain")
        self.assertEqual(completed_calls[0].kwargs["metadata"]["runtime"], "claude_code")
        # Metered with REAL tokens; usd_cost is left unresolved (None) since a
        # subscription turn has no platform-billed per-token price — never a
        # fabricated $0.00 "known" cost.
        mock_usage.assert_awaited_once()
        usage_kwargs = mock_usage.await_args.kwargs
        self.assertEqual(usage_kwargs["tokens_in"], 12)
        self.assertEqual(usage_kwargs["tokens_out"], 4)
        self.assertIsNone(usage_kwargs["usd_cost"])
        self.assertEqual(usage_kwargs["mode"], "cli_subscription")
        self.assertTrue(usage_kwargs["metadata"]["tokens_known"])

    def test_grok_build_happy_path_dispatches_and_ledgers_like_claude_code_codex(self):
        # xAI Grok Build / Cursor CLI addition (2026-07-24) — provider
        # entries resolve correctly through the SAME dispatch path, with no
        # separate code path or special-casing needed.
        response = {
            "result": {"text": "hello from grok", "model": "grok-build", "usage": {"input_tokens": 9, "output_tokens": 3}},
        }
        with (
            patch("server_modules.gateway_state_repository.get_gateway_registration", return_value=self._registration()),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value={"llm_runtimes": {"grok_build": {"installed": True, "authenticated": True}}},
            ),
            patch("server_modules.gateway_execution_service.execute_tool_via_gateway", new=AsyncMock(return_value=response)),
            patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock()) as mock_ledger,
            patch("server_modules.usage_events_repository.record_usage_event", new=AsyncMock()) as mock_usage,
        ):
            reply, usage, model = _run(
                sage_agent_runtime_service._dispatch_cli_subscription_gateway_brain(
                    workspace_id="ws-1", tenant_id="t-1", agent_id="agent-1",
                    gateway_binding="gateway-1", runtime="grok_build", model="grok-build",
                    system_prompt="Be terse.", user_message="hi",
                )
            )
        self.assertEqual(reply, "hello from grok")
        self.assertEqual(usage, {"input_tokens": 9, "output_tokens": 3})
        completed_calls = [c for c in mock_ledger.await_args_list if c.kwargs.get("action") == "gateway_brain_turn"]
        self.assertEqual(len(completed_calls), 1)
        self.assertEqual(completed_calls[0].kwargs["metadata"]["runtime"], "grok_build")
        usage_kwargs = mock_usage.await_args.kwargs
        self.assertEqual(usage_kwargs["provider"], "grok_build")
        self.assertIsNone(usage_kwargs["usd_cost"], "never a fabricated $0.00 known cost for a subscription runtime")

    def test_cursor_cli_not_authenticated_fails_loudly_never_falls_back_to_another_runtime(self):
        # This is the "reconnect needed, never a silent failure or fallback"
        # test at the dispatch layer: an unauthenticated cursor_cli binding
        # must raise a clear error naming cursor_cli specifically — it must
        # NEVER silently substitute claude_code/codex/platform_credits.
        with (
            patch("server_modules.gateway_state_repository.get_gateway_registration", return_value=self._registration()),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value={"llm_runtimes": {"cursor_cli": {"installed": True, "authenticated": False}}},
            ),
            patch("server_modules.gateway_execution_service.execute_tool_via_gateway", new=AsyncMock()) as mock_execute,
            patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.usage_events_repository.record_usage_event", new=AsyncMock()),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _run(
                    sage_agent_runtime_service._dispatch_cli_subscription_gateway_brain(
                        workspace_id="ws-1", tenant_id="t-1", agent_id="agent-1",
                        gateway_binding="gateway-1", runtime="cursor_cli", model="auto",
                        system_prompt="", user_message="hi",
                    )
                )
            mock_execute.assert_not_awaited()
        self.assertIn("Cursor CLI", str(ctx.exception))
        self.assertIn("Heads up:", str(ctx.exception))

    def test_reasoning_effort_reaches_the_gateway_arguments_when_set(self):
        """Phase 1 (reasoning-effort control): the value reaches the
        Gateway's llm.generate arguments dict as "reasoning_effort" --
        runtime.ts forwards it into cli-runner.ts's buildInvocation, which
        appends --effort (claude_code) or -c model_reasoning_effort=
        (codex)."""
        response = {"result": {"text": "hi", "usage": {}}}
        mock_execute = AsyncMock(return_value=response)
        with (
            patch("server_modules.gateway_state_repository.get_gateway_registration", return_value=self._registration()),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value=self._llm_runtimes_payload(),
            ),
            patch("server_modules.gateway_execution_service.execute_tool_via_gateway", new=mock_execute),
            patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.usage_events_repository.record_usage_event", new=AsyncMock()),
        ):
            _run(
                sage_agent_runtime_service._dispatch_cli_subscription_gateway_brain(
                    workspace_id="ws-1", tenant_id="t-1", agent_id="agent-1",
                    gateway_binding="gateway-1", runtime="claude_code", model="claude-sonnet-4-6",
                    system_prompt="Be terse.", user_message="hi", reasoning_effort="xhigh",
                )
            )
        sent_arguments = mock_execute.await_args.kwargs["arguments"]
        self.assertEqual(sent_arguments["reasoning_effort"], "xhigh")

    def test_reasoning_effort_key_omitted_entirely_when_unset(self):
        """Append-only-when-set -- an empty override means "let the CLI use
        its own configured default", never a fabricated/empty flag value
        appended on the Gateway side."""
        response = {"result": {"text": "hi", "usage": {}}}
        mock_execute = AsyncMock(return_value=response)
        with (
            patch("server_modules.gateway_state_repository.get_gateway_registration", return_value=self._registration()),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value=self._llm_runtimes_payload(),
            ),
            patch("server_modules.gateway_execution_service.execute_tool_via_gateway", new=mock_execute),
            patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.usage_events_repository.record_usage_event", new=AsyncMock()),
        ):
            _run(
                sage_agent_runtime_service._dispatch_cli_subscription_gateway_brain(
                    workspace_id="ws-1", tenant_id="t-1", agent_id="agent-1",
                    gateway_binding="gateway-1", runtime="claude_code", model="claude-sonnet-4-6",
                    system_prompt="Be terse.", user_message="hi",
                )
            )
        sent_arguments = mock_execute.await_args.kwargs["arguments"]
        self.assertNotIn("reasoning_effort", sent_arguments)

    def test_happy_path_with_unreported_usage_marks_tokens_unknown(self):
        """Truth in numbers: when the CLI doesn't report usage, the row is
        still recorded (never skipped) with an explicit tokens_known=False
        marker instead of inventing plausible-looking numbers."""
        response = {"result": {"text": "hi", "usage": {}}}
        with (
            patch("server_modules.gateway_state_repository.get_gateway_registration", return_value=self._registration()),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value=self._llm_runtimes_payload(),
            ),
            patch("server_modules.gateway_execution_service.execute_tool_via_gateway", new=AsyncMock(return_value=response)),
            patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.usage_events_repository.record_usage_event", new=AsyncMock()) as mock_usage,
        ):
            _reply, _usage, model = _run(
                sage_agent_runtime_service._dispatch_cli_subscription_gateway_brain(
                    workspace_id="ws-1", tenant_id="t-1", agent_id="agent-1",
                    gateway_binding="gateway-1", runtime="codex", model="",
                    system_prompt="", user_message="hi",
                )
            )
        self.assertEqual(model, "default")
        usage_kwargs = mock_usage.await_args.kwargs
        self.assertEqual(usage_kwargs["tokens_in"], 0)
        self.assertEqual(usage_kwargs["tokens_out"], 0)
        self.assertIsNone(usage_kwargs["usd_cost"])
        self.assertFalse(usage_kwargs["metadata"]["tokens_known"])

    def test_no_gateway_bound_raises_and_is_ledgered(self):
        with patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock()) as mock_ledger:
            with self.assertRaises(RuntimeError) as ctx:
                _run(
                    sage_agent_runtime_service._dispatch_cli_subscription_gateway_brain(
                        workspace_id="ws-1", tenant_id="t-1", agent_id="agent-1",
                        gateway_binding="", runtime="claude_code", model="",
                        system_prompt="", user_message="hi",
                    )
                )
        message = str(ctx.exception)
        self.assertIn("Heads up:", message)
        self.assertIn("requires a Gateway", message)
        failure_calls = [c for c in mock_ledger.await_args_list if c.kwargs.get("action") == "llm_generate_failed"]
        self.assertEqual(len(failure_calls), 1)
        self.assertEqual(failure_calls[0].kwargs["event_class"], "gateway_hardware")

    def test_gateway_offline_raises_and_is_ledgered(self):
        with (
            patch("server_modules.gateway_state_repository.get_gateway_registration", return_value=self._registration()),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value=self._llm_runtimes_payload(),
            ),
            patch(
                "server_modules.gateway_execution_service.execute_tool_via_gateway",
                new=AsyncMock(side_effect=ValueError("gateway_offline")),
            ),
            patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock()) as mock_ledger,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _run(
                    sage_agent_runtime_service._dispatch_cli_subscription_gateway_brain(
                        workspace_id="ws-1", tenant_id="t-1", agent_id="agent-1",
                        gateway_binding="gateway-1", runtime="claude_code", model="",
                        system_prompt="", user_message="hi",
                    )
                )
        message = str(ctx.exception)
        self.assertIn("Heads up:", message)
        self.assertIn("offline", message.lower())
        failure_calls = [c for c in mock_ledger.await_args_list if c.kwargs.get("action") == "llm_generate_failed"]
        self.assertEqual(len(failure_calls), 1)

    def test_cli_not_installed_raises_before_ever_dispatching(self):
        """Not-installed is caught by the readiness pre-check — the Gateway
        WSS rail is never even invoked for a CLI we already know isn't
        there."""
        with (
            patch("server_modules.gateway_state_repository.get_gateway_registration", return_value=self._registration()),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value=self._llm_runtimes_payload(claude_code=False),
            ),
            patch("server_modules.gateway_execution_service.execute_tool_via_gateway", new=AsyncMock()) as mock_dispatch,
            patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock()) as mock_ledger,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _run(
                    sage_agent_runtime_service._dispatch_cli_subscription_gateway_brain(
                        workspace_id="ws-1", tenant_id="t-1", agent_id="agent-1",
                        gateway_binding="gateway-1", runtime="claude_code", model="",
                        system_prompt="", user_message="hi",
                    )
                )
        message = str(ctx.exception)
        self.assertIn("Heads up:", message)
        self.assertIn("not installed", message.lower())
        self.assertIn("npm install -g @anthropic-ai/claude-code", message)
        mock_dispatch.assert_not_awaited()
        failure_calls = [c for c in mock_ledger.await_args_list if c.kwargs.get("action") == "llm_generate_failed"]
        self.assertEqual(len(failure_calls), 1)

    def test_codex_not_authenticated_raises_with_codex_specific_login_command(self):
        with (
            patch("server_modules.gateway_state_repository.get_gateway_registration", return_value=self._registration()),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value={"llm_runtimes": {"codex": {"installed": True, "authenticated": False}}},
            ),
            patch("server_modules.gateway_execution_service.execute_tool_via_gateway", new=AsyncMock()) as mock_dispatch,
            patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock()),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _run(
                    sage_agent_runtime_service._dispatch_cli_subscription_gateway_brain(
                        workspace_id="ws-1", tenant_id="t-1", agent_id="agent-1",
                        gateway_binding="gateway-1", runtime="codex", model="",
                        system_prompt="", user_message="hi",
                    )
                )
        message = str(ctx.exception)
        self.assertIn("Heads up:", message)
        self.assertIn("not logged in", message.lower())
        self.assertIn("codex login", message)
        mock_dispatch.assert_not_awaited()

    def test_timeout_raises_and_is_ledgered(self):
        with (
            patch("server_modules.gateway_state_repository.get_gateway_registration", return_value=self._registration()),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value=self._llm_runtimes_payload(),
            ),
            patch(
                "server_modules.gateway_execution_service.execute_tool_via_gateway",
                new=AsyncMock(side_effect=Exception(
                    "Claude Code generation timed out on this Gateway (no response within 120000ms)."
                )),
            ),
            patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock()) as mock_ledger,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _run(
                    sage_agent_runtime_service._dispatch_cli_subscription_gateway_brain(
                        workspace_id="ws-1", tenant_id="t-1", agent_id="agent-1",
                        gateway_binding="gateway-1", runtime="claude_code", model="",
                        system_prompt="", user_message="hi",
                    )
                )
        message = str(ctx.exception)
        self.assertIn("Heads up:", message)
        self.assertIn("timed out", message.lower())
        failure_calls = [c for c in mock_ledger.await_args_list if c.kwargs.get("action") == "llm_generate_failed"]
        self.assertEqual(len(failure_calls), 1)

    def test_crash_raises_and_is_ledgered(self):
        with (
            patch("server_modules.gateway_state_repository.get_gateway_registration", return_value=self._registration()),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value=self._llm_runtimes_payload(),
            ),
            patch(
                "server_modules.gateway_execution_service.execute_tool_via_gateway",
                new=AsyncMock(side_effect=Exception(
                    "Codex exited unexpectedly on this Gateway (codex exited with code 139)."
                )),
            ),
            patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock()) as mock_ledger,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _run(
                    sage_agent_runtime_service._dispatch_cli_subscription_gateway_brain(
                        workspace_id="ws-1", tenant_id="t-1", agent_id="agent-1",
                        gateway_binding="gateway-1", runtime="codex", model="",
                        system_prompt="", user_message="hi",
                    )
                )
        message = str(ctx.exception)
        self.assertIn("Heads up:", message)
        self.assertIn("exited unexpectedly", message.lower())
        failure_calls = [c for c in mock_ledger.await_args_list if c.kwargs.get("action") == "llm_generate_failed"]
        self.assertEqual(len(failure_calls), 1)

    def test_unsupported_runtime_rejected_before_any_lookup(self):
        with patch("server_modules.gateway_state_repository.get_gateway_registration") as mock_get_reg:
            with self.assertRaises(RuntimeError) as ctx:
                _run(
                    sage_agent_runtime_service._dispatch_cli_subscription_gateway_brain(
                        workspace_id="ws-1", tenant_id="t-1", agent_id="agent-1",
                        gateway_binding="gateway-1", runtime="gpt-5-direct", model="",
                        system_prompt="", user_message="hi",
                    )
                )
        self.assertIn("not a supported cli_subscription runtime", str(ctx.exception))
        mock_get_reg.assert_not_called()

    def test_empty_completion_treated_as_crash(self):
        with (
            patch("server_modules.gateway_state_repository.get_gateway_registration", return_value=self._registration()),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value=self._llm_runtimes_payload(),
            ),
            patch(
                "server_modules.gateway_execution_service.execute_tool_via_gateway",
                new=AsyncMock(return_value={"result": {"text": "", "usage": {}}}),
            ),
            patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock()) as mock_ledger,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _run(
                    sage_agent_runtime_service._dispatch_cli_subscription_gateway_brain(
                        workspace_id="ws-1", tenant_id="t-1", agent_id="agent-1",
                        gateway_binding="gateway-1", runtime="claude_code", model="",
                        system_prompt="", user_message="hi",
                    )
                )
        self.assertIn("exited unexpectedly", str(ctx.exception).lower())
        failure_calls = [c for c in mock_ledger.await_args_list if c.kwargs.get("action") == "llm_generate_failed"]
        self.assertEqual(len(failure_calls), 1)


class CliSubscriptionReadinessReasonTests(unittest.TestCase):
    """Direct unit coverage for the runtime-specific readiness check. Generic
    Gateway-online checks are gateway_execution_service's job (already
    covered by its own tests); this is the NEW per-runtime (claude_code vs
    codex) piece cli_subscription needed on top of that."""

    def test_missing_registration(self):
        reason = sage_agent_runtime_service._cli_subscription_readiness_reason(
            None, workspace_id="ws-1", runtime="claude_code",
        )
        self.assertEqual(reason, "gateway_registration_missing")

    def test_inactive_registration(self):
        reg = {"status": "inactive", "workspace_id": "ws-1"}
        reason = sage_agent_runtime_service._cli_subscription_readiness_reason(reg, workspace_id="ws-1", runtime="claude_code")
        self.assertEqual(reason, "gateway_registration_inactive")

    def test_revoked_registration(self):
        reg = {"status": "active", "device_trust_state": "revoked", "workspace_id": "ws-1"}
        reason = sage_agent_runtime_service._cli_subscription_readiness_reason(reg, workspace_id="ws-1", runtime="claude_code")
        self.assertEqual(reason, "gateway_device_revoked")

    def test_workspace_mismatch(self):
        reg = {"status": "active", "workspace_id": "ws-other"}
        reason = sage_agent_runtime_service._cli_subscription_readiness_reason(reg, workspace_id="ws-1", runtime="claude_code")
        self.assertEqual(reason, "gateway_workspace_mismatch")

    def test_not_installed(self):
        reg = {"status": "active", "workspace_id": "ws-1", "gateway_id": "gw-1"}
        with patch(
            "server_modules.gateway_registry_service.gateway_registration_public_payload",
            return_value={"llm_runtimes": {"codex": {"installed": False, "authenticated": False}}},
        ):
            reason = sage_agent_runtime_service._cli_subscription_readiness_reason(reg, workspace_id="ws-1", runtime="codex")
        self.assertEqual(reason, "codex_not_installed")

    def test_not_authenticated(self):
        reg = {"status": "active", "workspace_id": "ws-1", "gateway_id": "gw-1"}
        with patch(
            "server_modules.gateway_registry_service.gateway_registration_public_payload",
            return_value={"llm_runtimes": {"claude_code": {"installed": True, "authenticated": False}}},
        ):
            reason = sage_agent_runtime_service._cli_subscription_readiness_reason(reg, workspace_id="ws-1", runtime="claude_code")
        self.assertEqual(reason, "claude_code_not_authenticated")

    def test_ready(self):
        reg = {"status": "active", "workspace_id": "ws-1", "gateway_id": "gw-1"}
        with patch(
            "server_modules.gateway_registry_service.gateway_registration_public_payload",
            return_value={"llm_runtimes": {"codex": {"installed": True, "authenticated": True}}},
        ):
            reason = sage_agent_runtime_service._cli_subscription_readiness_reason(reg, workspace_id="ws-1", runtime="codex")
        self.assertEqual(reason, "")

    def test_grok_build_not_installed(self):
        reg = {"status": "active", "workspace_id": "ws-1", "gateway_id": "gw-1"}
        with patch(
            "server_modules.gateway_registry_service.gateway_registration_public_payload",
            return_value={"llm_runtimes": {"grok_build": {"installed": False, "authenticated": False}}},
        ):
            reason = sage_agent_runtime_service._cli_subscription_readiness_reason(reg, workspace_id="ws-1", runtime="grok_build")
        self.assertEqual(reason, "grok_build_not_installed")

    def test_grok_build_installed_but_not_authenticated_is_the_expired_credential_reconnect_case(self):
        # This is the core "an expired credential produces a visible
        # reconnect state" signal: installed=True (the binary is there,
        # auth.json exists or existed) but authenticated=False (the CLI's own
        # background refresh already failed — see cli-login-session.ts's
        # grok_build comment). Never confused with "not_installed", never a
        # silent pass-through.
        reg = {"status": "active", "workspace_id": "ws-1", "gateway_id": "gw-1"}
        with patch(
            "server_modules.gateway_registry_service.gateway_registration_public_payload",
            return_value={"llm_runtimes": {"grok_build": {"installed": True, "authenticated": False}}},
        ):
            reason = sage_agent_runtime_service._cli_subscription_readiness_reason(reg, workspace_id="ws-1", runtime="grok_build")
        self.assertEqual(reason, "grok_build_not_authenticated")

    def test_cursor_cli_not_installed_and_not_authenticated(self):
        reg = {"status": "active", "workspace_id": "ws-1", "gateway_id": "gw-1"}
        with patch(
            "server_modules.gateway_registry_service.gateway_registration_public_payload",
            return_value={"llm_runtimes": {"cursor_cli": {"installed": False, "authenticated": False}}},
        ):
            reason = sage_agent_runtime_service._cli_subscription_readiness_reason(reg, workspace_id="ws-1", runtime="cursor_cli")
        self.assertEqual(reason, "cursor_cli_not_installed")

        with patch(
            "server_modules.gateway_registry_service.gateway_registration_public_payload",
            return_value={"llm_runtimes": {"cursor_cli": {"installed": True, "authenticated": False}}},
        ):
            reason = sage_agent_runtime_service._cli_subscription_readiness_reason(reg, workspace_id="ws-1", runtime="cursor_cli")
        self.assertEqual(reason, "cursor_cli_not_authenticated")

    def test_grok_build_and_cursor_cli_ready(self):
        reg = {"status": "active", "workspace_id": "ws-1", "gateway_id": "gw-1"}
        for runtime in ("grok_build", "cursor_cli"):
            with patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value={"llm_runtimes": {runtime: {"installed": True, "authenticated": True}}},
            ):
                reason = sage_agent_runtime_service._cli_subscription_readiness_reason(reg, workspace_id="ws-1", runtime=runtime)
            self.assertEqual(reason, "", f"{runtime} should read ready when installed+authenticated")


class FriendlyCliSubscriptionErrorTests(unittest.TestCase):
    """Every G5 condition gets its own distinct, platform-voiced
    ("Heads up: ...") message — never one blanket string."""

    def test_each_condition_gets_a_distinct_message(self):
        cases = [
            ("no_gateway_bound", "claude_code", "requires a gateway"),
            ("gateway_registration_missing", "claude_code", "no longer paired"),
            ("gateway_offline", "claude_code", "offline"),
            ("claude_code_not_installed", "claude_code", "not installed"),
            ("codex_not_installed", "codex", "not installed"),
            ("claude_code_not_authenticated", "claude_code", "not logged in"),
            ("codex_not_authenticated", "codex", "not logged in"),
            ("Claude Code generation timed out on this Gateway (...)", "claude_code", "timed out"),
            ("Codex exited unexpectedly on this Gateway (...)", "codex", "exited unexpectedly"),
        ]
        messages = set()
        for reason, runtime, expect_substring in cases:
            message = sage_agent_runtime_service._friendly_cli_subscription_error(reason, runtime=runtime)
            self.assertIn("Heads up:", message)
            self.assertIn(expect_substring, message.lower())
            messages.add(message)
        # Every condition produced a genuinely distinct message — never one
        # blanket string standing in for all of them.
        self.assertEqual(len(messages), len(cases))

    def test_claude_vs_codex_not_installed_reference_the_right_cli_and_install_command(self):
        claude_msg = sage_agent_runtime_service._friendly_cli_subscription_error("claude_code_not_installed", runtime="claude_code")
        codex_msg = sage_agent_runtime_service._friendly_cli_subscription_error("codex_not_installed", runtime="codex")
        self.assertIn("Claude Code", claude_msg)
        self.assertIn("@anthropic-ai/claude-code", claude_msg)
        self.assertIn("Codex", codex_msg)
        self.assertIn("@openai/codex", codex_msg)

    def test_claude_vs_codex_login_commands(self):
        claude_msg = sage_agent_runtime_service._friendly_cli_subscription_error("claude_code_not_authenticated", runtime="claude_code")
        codex_msg = sage_agent_runtime_service._friendly_cli_subscription_error("codex_not_authenticated", runtime="codex")
        self.assertIn("claude login", claude_msg)
        self.assertIn("codex login", codex_msg)

    def test_unknown_reason_falls_back_to_an_honest_message_not_a_silent_generic_one(self):
        message = sage_agent_runtime_service._friendly_cli_subscription_error("some_never_seen_reason", runtime="claude_code")
        self.assertIn("Heads up:", message)
        self.assertIn("some_never_seen_reason", message)

    def test_grok_build_and_cursor_cli_get_their_own_distinct_not_installed_and_not_authenticated_messages(self):
        # xAI Grok Build / Cursor CLI addition (2026-07-24) — same G5
        # guarantee as claude_code/codex: every distinct failure mode for the
        # two new runtimes gets its own honest message, never collapsed into
        # the claude_code/codex fallback.
        grok_not_installed = sage_agent_runtime_service._friendly_cli_subscription_error(
            "grok_build_not_installed", runtime="grok_build",
        )
        cursor_not_installed = sage_agent_runtime_service._friendly_cli_subscription_error(
            "cursor_cli_not_installed", runtime="cursor_cli",
        )
        grok_not_authenticated = sage_agent_runtime_service._friendly_cli_subscription_error(
            "grok_build_not_authenticated", runtime="grok_build",
        )
        cursor_not_authenticated = sage_agent_runtime_service._friendly_cli_subscription_error(
            "cursor_cli_not_authenticated", runtime="cursor_cli",
        )
        self.assertIn("Grok Build", grok_not_installed)
        self.assertIn("x.ai/cli/install.sh", grok_not_installed)
        self.assertIn("Cursor CLI", cursor_not_installed)
        self.assertIn("cursor.com/install", cursor_not_installed)
        self.assertIn("Grok Build", grok_not_authenticated)
        self.assertIn("grok login --device-auth", grok_not_authenticated)
        self.assertIn("Cursor CLI", cursor_not_authenticated)
        self.assertIn("cursor-agent login", cursor_not_authenticated)
        # This IS the "expired credential produces a visible reconnect state"
        # test at the platform-voice layer: every one of these four messages
        # is a distinct, non-empty, explicit "Heads up: ..." string — never a
        # silent no-op and never a fallback to claude_code/codex's wording.
        messages = {grok_not_installed, cursor_not_installed, grok_not_authenticated, cursor_not_authenticated}
        self.assertEqual(len(messages), 4)
        for message in messages:
            self.assertIn("Heads up:", message)

    def test_unsupported_runtime_message_lists_all_four_valid_runtimes(self):
        message = sage_agent_runtime_service._friendly_cli_subscription_error(
            "unsupported cli_subscription runtime: gemini_cli", runtime="gemini_cli",
        )
        self.assertIn("Heads up:", message)
        for runtime in ("claude_code", "codex", "grok_build", "cursor_cli"):
            self.assertIn(runtime, message)

    def test_valid_cli_subscription_runtimes_includes_all_four(self):
        self.assertEqual(
            sage_agent_runtime_service._VALID_CLI_SUBSCRIPTION_RUNTIMES,
            {"claude_code", "codex", "grok_build", "cursor_cli"},
        )


class CliSubscriptionProviderRejectionClassificationTests(unittest.TestCase):
    """URGENT fix (2026-08-14): a real customer's chat showed the raw
    provider JSON verbatim ("Heads up: Codex exited unexpectedly —
    {"type":"error","status":400,"error":{"type":"invalid_request_error",
    "message":"The 'gpt-5.4' model is not supported when using Codex with a
    ChatGPT account."}}") because Codex's own app-server sometimes forwards
    the underlying provider error body as its notification message verbatim.
    _classify_cli_subscription_provider_rejection must turn that into a
    clean, actionable message and never let the JSON reach the screen."""

    def test_the_exact_live_bug_produces_a_clean_actionable_message(self):
        reason = (
            "Codex exited unexpectedly on this Gateway "
            '({"type":"error","status":400,"error":{"type":"invalid_request_error",'
            '"message":"The \'gpt-5.4\' model is not supported when using Codex with a '
            'ChatGPT account."}}).'
        )
        message = sage_agent_runtime_service._classify_cli_subscription_provider_rejection(
            reason, model="gpt-5.4", runtime="codex",
        )
        self.assertIsNotNone(message)
        self.assertIn("Heads up:", message)
        self.assertIn("gpt-5.4", message)
        self.assertIn("Model tab", message)
        # The whole point: no raw JSON syntax reaches the customer.
        self.assertNotIn("{", message)
        self.assertNotIn("}", message)
        self.assertNotIn("invalid_request_error", message)
        self.assertNotIn('"type"', message)

    def test_other_invalid_request_errors_still_get_a_clean_message_without_presupposing_the_model(self):
        reason = (
            "Codex exited unexpectedly on this Gateway "
            '({"type":"error","status":400,"error":{"type":"invalid_request_error",'
            '"message":"reasoning effort \'ultra\' is not a recognized value."}}).'
        )
        message = sage_agent_runtime_service._classify_cli_subscription_provider_rejection(
            reason, model="gpt-5.6-terra", runtime="codex",
        )
        self.assertIsNotNone(message)
        self.assertIn("Heads up:", message)
        self.assertIn("reasoning effort", message)
        self.assertNotIn("{", message)
        self.assertNotIn("}", message)

    def test_a_reason_with_no_embedded_json_falls_through_to_the_generic_classifier(self):
        message = sage_agent_runtime_service._classify_cli_subscription_provider_rejection(
            "Codex exited unexpectedly on this Gateway (segfault).", model="gpt-5.2", runtime="codex",
        )
        self.assertIsNone(message)

    def test_falling_through_still_composes_a_full_message_via_the_existing_classifier(self):
        # Proves the two functions compose the way the real raise site does —
        # a None from the structural classifier must not leave the caller
        # with nothing to show.
        reason = "Codex exited unexpectedly on this Gateway (segfault)."
        self.assertIsNone(
            sage_agent_runtime_service._classify_cli_subscription_provider_rejection(
                reason, model="gpt-5.2", runtime="codex",
            )
        )
        fallback = sage_agent_runtime_service._friendly_cli_subscription_error(reason, runtime="codex")
        self.assertIn("Heads up:", fallback)
        self.assertIn("segfault", fallback)


class SageAgentRuntimeSpecialistProviderResolutionTests(unittest.TestCase):
    """§25.3/§28.2 — a specialist with its OWN provider/model_config binding
    must resolve provider AND credentials TOGETHER via
    _resolve_agent_cloud_provider, not have only the provider LABEL swapped
    onto the workspace's default credentials (the exact bug that made a BYOK
    specialist's turn silently run under the workspace's/another agent's
    key, mislabeled as its own provider). Opt-in per agent: a specialist
    with nothing configured, and Sage's own turn (specialist_context=None),
    must take the exact unchanged workspace-default path -- proven below by
    making the per-agent resolver explode if it's ever called for them, not
    just by checking a return value."""

    @staticmethod
    def _spec(**overrides):
        base = dict(
            agent_install_id="agent-byok-1",
            agent_label="Research Agent",
            agent_kind="specialist",
            persona="You are a research specialist.",
        )
        base.update(overrides)
        return SpecialistRuntimeContext(**base)

    @staticmethod
    def _run_chat(specialist_context=None, mock_agent_provider=None, mock_workspace_provider=None):
        """Full handle_sage_chat harness for a specialist turn. Specialists
        (unlike Sage's own plain-chat path) run through
        _run_sage_action_loop_v3 -> direct_chat_generation_service.
        stream_provider_backed_direct_chat -- a different, tool-capable
        generation entry point than generate_chat_reply_with_provider_fallback,
        confirmed by tracing the actual call chain (handle_sage_chat:4180's
        _run_sage_action_loop_v3 call passes the SAME provider/credentials
        variables this fix resolves straight through to that stream call's
        context/metadata kwargs). Mocked here exactly like the existing
        test_activity_event_emitted_as_failed_on_provider_error does, so
        the resolved provider/credentials can be inspected without a real
        network call."""
        mock_agent_provider = mock_agent_provider or AsyncMock(
            return_value=("anthropic", {"api_key": "sk-agent-own-key"}, "byok_api")
        )
        mock_workspace_provider = mock_workspace_provider or AsyncMock(
            return_value=("deepseek", {"api_key": "sk-workspace-default"})
        )
        stream_events = [{
            "type": "final",
            "payload": {"reply": "Reply", "actions": [], "error": None},
        }]
        with (
            patch(
                "server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile",
                return_value={"profile": {"user_name": "", "identity_summary": "", "communication_style": "", "recurring_responsibility": "", "standing_rules": []}},
            ),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.memory_service.get_memory", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", new=mock_workspace_provider),
            patch("server_modules.sage_agent_runtime_service._resolve_agent_cloud_provider", new=mock_agent_provider),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability",
                return_value={"runtime_ok": True, "local_gateway_online": True},
            ),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat",
            ) as mock_stream,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            mock_stream.return_value = iter(stream_events)
            _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="hello",
                specialist_context=specialist_context,
            ))
        return mock_agent_provider, mock_workspace_provider, mock_stream

    def test_byok_specialist_resolves_provider_and_credentials_together(self):
        spec = self._spec(provider="anthropic", model="claude-sonnet-4-6", mode="byok_api")
        mock_agent_provider, _mock_ws, mock_stream = self._run_chat(specialist_context=spec)

        mock_agent_provider.assert_awaited_once()
        call_args = mock_agent_provider.await_args
        self.assertEqual(call_args.args[0], "ws-1")
        self.assertEqual(call_args.args[1]["mode"], "byok_api")
        self.assertEqual(call_args.args[1]["provider"], "anthropic")
        self.assertEqual(call_args.args[2], "agent-byok-1")

        # The actual generation call must receive THIS agent's own resolved
        # credentials, not the workspace default.
        gen_kwargs = mock_stream.call_args.kwargs
        self.assertEqual(gen_kwargs["context"]["provider"], "anthropic")
        self.assertEqual(gen_kwargs["metadata"]["credentials"], {"api_key": "sk-agent-own-key"})
        self.assertNotEqual(gen_kwargs["metadata"]["credentials"], {"api_key": "sk-workspace-default"})

    def test_two_specialists_two_keys_each_turn_uses_its_own(self):
        """The task's literal VERIFY ask: two agents, two different keys,
        each turn proven to use the right one."""
        anthropic_spec = self._spec(
            agent_install_id="agent-anthropic-1", provider="anthropic", mode="byok_api",
        )
        openai_spec = self._spec(
            agent_install_id="agent-openai-1", provider="openai", mode="byok_api",
        )

        anthropic_resolver = AsyncMock(return_value=("anthropic", {"api_key": "sk-anthropic-key"}, "byok_api"))
        _, _, stream_1 = self._run_chat(specialist_context=anthropic_spec, mock_agent_provider=anthropic_resolver)
        self.assertEqual(stream_1.call_args.kwargs["metadata"]["credentials"], {"api_key": "sk-anthropic-key"})
        self.assertEqual(anthropic_resolver.await_args.args[2], "agent-anthropic-1")

        openai_resolver = AsyncMock(return_value=("openai", {"api_key": "sk-openai-key"}, "byok_api"))
        _, _, stream_2 = self._run_chat(specialist_context=openai_spec, mock_agent_provider=openai_resolver)
        self.assertEqual(stream_2.call_args.kwargs["metadata"]["credentials"], {"api_key": "sk-openai-key"})
        self.assertEqual(openai_resolver.await_args.args[2], "agent-openai-1")

        # Neither turn's credentials leaked into the other's.
        self.assertNotEqual(
            stream_1.call_args.kwargs["metadata"]["credentials"],
            stream_2.call_args.kwargs["metadata"]["credentials"],
        )

    def test_legacy_specialist_with_only_provider_set_defaults_to_byok(self):
        """Pre-model_config specialists only ever had `.provider` set, no
        `.mode` -- must still be treated as byok_api (a provider override
        with no mode is meaningless as anything else), not silently
        swallowed into platform_credits, which would drop the provider
        override entirely and use the workspace's key under a foreign
        label -- the exact regression risk of naively defaulting empty
        mode to "platform_credits"."""
        spec = self._spec(provider="openai", mode="")
        mock_agent_provider, _mock_ws, _mock_stream = self._run_chat(specialist_context=spec)

        mock_agent_provider.assert_awaited_once()
        self.assertEqual(mock_agent_provider.await_args.args[1]["mode"], "byok_api")
        self.assertEqual(mock_agent_provider.await_args.args[1]["provider"], "openai")

    def test_specialist_with_nothing_configured_uses_unchanged_workspace_default(self):
        """The common-path guarantee: an agent with no model_config override
        at all must NEVER touch _resolve_agent_cloud_provider -- proven by
        making it explode if called, not just by checking it wasn't called
        (the difference matters: a call that happens to return harmlessly
        would still hide a real regression)."""
        spec = self._spec(provider="", model="", mode="")
        exploding = AsyncMock(side_effect=AssertionError("must not be called for an unconfigured specialist"))
        _mock_agent, mock_workspace_provider, mock_stream = self._run_chat(
            specialist_context=spec, mock_agent_provider=exploding,
        )

        mock_workspace_provider.assert_awaited_once()
        self.assertEqual(mock_stream.call_args.kwargs["context"]["provider"], "deepseek")

    def test_sage_own_turn_unaffected_specialist_context_none(self):
        """Sage's own turn (specialist_context=None) is the other half of
        the common path -- must also never touch the per-agent resolver."""
        exploding = AsyncMock(side_effect=AssertionError("must not be called for Sage's own turn"))
        _mock_agent, mock_workspace_provider, mock_stream = self._run_chat(
            specialist_context=None, mock_agent_provider=exploding,
        )

        mock_workspace_provider.assert_awaited_once()
        self.assertEqual(mock_stream.call_args.kwargs["context"]["provider"], "deepseek")

    def test_platform_credits_specialist_uses_shared_resolver_but_workspace_result(self):
        """An agent explicitly set to platform_credits mode opts into the
        new resolver (it has SOMETHING configured), but
        _resolve_agent_cloud_provider's platform_credits branch is a pure
        passthrough to the same workspace resolution -- so the end result
        must be identical to the unconfigured-agent path, not some third
        behavior."""
        spec = self._spec(provider="", model="", mode="platform_credits")
        mock_agent_provider = AsyncMock(return_value=("deepseek", {"api_key": "sk-workspace-default"}, "platform_credits"))
        _mock_agent, _mock_ws, mock_stream = self._run_chat(
            specialist_context=spec, mock_agent_provider=mock_agent_provider,
        )

        mock_agent_provider.assert_awaited_once()
        self.assertEqual(mock_agent_provider.await_args.args[1]["mode"], "platform_credits")
        self.assertEqual(mock_stream.call_args.kwargs["context"]["provider"], "deepseek")

    def test_local_mode_specialist_never_touches_cloud_resolver(self):
        """local/cli_subscription specialists dispatch entirely separately
        (gateway WSS rail) and must never reach _resolve_agent_cloud_provider
        at the cloud-provider-resolution point -- that resolver returns a
        differently-shaped tuple for these modes ((runtime,
        {gateway_binding}, mode), not (provider, credentials)) and calling
        it here would be both wrong and pointless, since the dedicated
        gateway-dispatch branch below returns before either value is read."""
        spec = self._spec(provider="", model="llama3", mode="local", gateway_binding="gw-1", runtime="ollama")
        exploding = AsyncMock(side_effect=AssertionError("must not be called for local mode"))
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.memory_service.get_memory", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", new=AsyncMock(return_value=("deepseek", {"api_key": "sk-workspace-default"}))),
            patch("server_modules.sage_agent_runtime_service._resolve_agent_cloud_provider", new=exploding),
            patch(
                "server_modules.sage_agent_runtime_service._dispatch_local_gateway_brain",
                new=AsyncMock(return_value=("local reply", {}, "llama3")),
            ),
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="hello", specialist_context=spec,
            ))
        self.assertEqual(result["message"], "local reply")


class SageAgentRuntimeMasterModelConfigCheckTests(unittest.TestCase):
    """Bug 2 (MAN-312-adjacent) regression.

    handle_sage_chat's own call to _resolve_cloud_provider (the "--- Call
    provider ---" line) is the ONE call site meant to resolve Sage's own
    turn -- see that function's own docstring: "Callers resolving Sage's
    OWN turn should pass True explicitly". Before this fix it always relied
    on check_master_model_config's default (False), so the HONEST BLOCK
    _resolve_cloud_provider implements for a master install misconfigured
    to cli_subscription/local (see test_core_loop_no_fallback.py's
    NoFallbackProviderResolutionTests, which exercises that block in
    isolation) never actually fired from a real handle_sage_chat call: a
    master agent (e.g. one renamed away from "Sage" in the Fleet UI, shown
    with a "CLI-subscription" gateway binding) whose Model tab was saved as
    cli_subscription kept silently resolving the platform DeepSeek default
    here, with no error anywhere -- production logs showed its turns
    dispatching through claude_agent_sdk_bridge with provider='deepseek'
    despite the Fleet UI claiming CLI-subscription.

    `_spec is None` is the correct signal for "this call is resolving the
    master's own turn": specialist_runtime_context.resolve_specialist_
    runtime_context returns None both when no active install was given AND
    when the active install IS the workspace master ("the master (Sage)
    runs its normal runtime") -- never for a genuine specialist. A genuine
    specialist must still get check_master_model_config=False, unchanged,
    so an unrelated misconfiguration on Sage's own card can never cross-
    contaminate a specialist's turn (SageAgentRuntimeSpecialistProvider
    ResolutionTests already covers that half from the specialist side)."""

    def test_master_turn_opts_into_the_master_model_config_check(self):
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile") as mock_profile,
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files") as mock_files,
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block") as mock_mem,
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch(
                "server_modules.sage_agent_runtime_service._resolve_cloud_provider",
                new=AsyncMock(return_value=("openai", {"api_key": "test-key"})),
            ) as mock_provider,
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback") as mock_generate,
            patched_provider_calls(reply="Hello there", usage={"model": "gpt-4o"}, provider="openai"),
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            mock_profile.return_value = {"profile": {"user_name": "", "identity_summary": "", "communication_style": "", "recurring_responsibility": "", "standing_rules": []}}
            mock_files.return_value = {}
            mock_mem.return_value = ""
            mock_generate.return_value = ("Hello there", {"model": "gpt-4o"}, "openai", "")

            _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="hi",
            ))

        mock_provider.assert_awaited_once_with("ws-1", check_master_model_config=True)

    def test_specialist_turn_does_not_check_master_model_config(self):
        spec = SpecialistRuntimeContext(
            agent_install_id="agent-1",
            agent_label="Research Agent",
            agent_kind="specialist",
            persona="You are a research specialist.",
        )
        stream_events = [{
            "type": "final",
            "payload": {"reply": "Reply", "actions": [], "error": None},
        }]
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.memory_service.get_memory", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch(
                "server_modules.sage_agent_runtime_service._resolve_cloud_provider",
                new=AsyncMock(return_value=("deepseek", {"api_key": "sk-workspace-default"})),
            ) as mock_provider,
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability",
                return_value={"runtime_ok": True, "local_gateway_online": True},
            ),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat",
            ) as mock_stream,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            mock_stream.return_value = iter(stream_events)
            _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="hello", specialist_context=spec,
            ))

        mock_provider.assert_awaited_once_with("ws-1", check_master_model_config=False)

    def test_master_turn_with_cli_subscription_model_config_raises_honest_error(self):
        """End-to-end proof at the handle_sage_chat level (not just the
        isolated _resolve_cloud_provider unit tests): a workspace whose
        master/Sage install was saved with model_config.mode=
        "cli_subscription" (the Fleet Model tab has no master/operator
        guard, so that PATCH succeeds) must now surface an honest error
        naming cli_subscription the moment its own turn resolves a
        provider -- never silently fall through to the platform DeepSeek
        default the way production logs showed."""
        master_install = {
            "id": "sage-main-1",
            "metadata": {"model_config": {"mode": "cli_subscription", "runtime": "codex"}},
        }
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch(
                "server_modules.control_plane_repository.resolve_tenant_id_for_workspace",
                new=AsyncMock(return_value="tenant-1"),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_master_agent_install",
                new=AsyncMock(return_value=master_install),
            ),
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _run(sage_agent_runtime_service.handle_sage_chat(
                    workspace_id="ws-1", message="hi",
                ))

        self.assertIn("cli_subscription", str(ctx.exception))


class SageAgentRuntimeReasoningEffortResolutionTests(unittest.TestCase):
    """Fleet Model tab's reasoning-effort picker (model_config.
    reasoning_effort) -- proves the value actually reaches the generation
    call (stream_provider_backed_direct_chat's normalized_reasoning_effort
    kwarg) instead of being silently discarded, the way it was hardcoded to
    "" before this fix regardless of what model_config said. Reuses
    SageAgentRuntimeSpecialistProviderResolutionTests's `_spec`/`_run_chat`
    static helpers directly (not by subclassing it, which would re-run its
    provider/credentials tests a second time under this class too)."""

    _spec = staticmethod(SageAgentRuntimeSpecialistProviderResolutionTests._spec)
    _run_chat = staticmethod(SageAgentRuntimeSpecialistProviderResolutionTests._run_chat)

    def test_byok_specialist_reasoning_effort_reaches_generation_call(self):
        spec = self._spec(provider="openai", model="gpt-5.4", mode="byok_api", reasoning_effort="high")
        _mock_agent, _mock_ws, mock_stream = self._run_chat(specialist_context=spec)

        gen_kwargs = mock_stream.call_args.kwargs
        self.assertEqual(gen_kwargs["normalized_reasoning_effort"], "high")

    def test_platform_credits_specialist_reasoning_effort_reaches_generation_call(self):
        spec = self._spec(provider="", model="", mode="platform_credits", reasoning_effort="low")
        mock_agent_provider = AsyncMock(return_value=("deepseek", {"api_key": "sk-workspace-default"}, "platform_credits"))
        _mock_agent, _mock_ws, mock_stream = self._run_chat(
            specialist_context=spec, mock_agent_provider=mock_agent_provider,
        )

        gen_kwargs = mock_stream.call_args.kwargs
        self.assertEqual(gen_kwargs["normalized_reasoning_effort"], "low")

    def test_xhigh_is_a_valid_level_not_a_typo_for_high(self):
        """provider_profiles.py's PROVIDER_MODEL_CATALOG genuinely lists
        "xhigh" as a distinct reasoning level for GPT-5.x/Codex-class models
        (see reasoning_levels on e.g. openai/gpt-5.4) -- must round-trip
        unmolested, not get clamped to "high"."""
        spec = self._spec(provider="openai", model="gpt-5.4", mode="byok_api", reasoning_effort="xhigh")
        _mock_agent, _mock_ws, mock_stream = self._run_chat(specialist_context=spec)

        self.assertEqual(mock_stream.call_args.kwargs["normalized_reasoning_effort"], "xhigh")

    def test_unset_reasoning_effort_passes_none_not_empty_string(self):
        """No override configured -- must reach the generation call as None
        (stream_provider_backed_direct_chat's own falsy check treats "" and
        None identically, but None matches the function's own
        Optional[str] contract instead of a magic empty-string sentinel)."""
        spec = self._spec(provider="anthropic", model="claude-sonnet-4-6", mode="byok_api")
        _mock_agent, _mock_ws, mock_stream = self._run_chat(specialist_context=spec)

        self.assertIsNone(mock_stream.call_args.kwargs["normalized_reasoning_effort"])

    def test_invalid_reasoning_effort_is_dropped_not_passed_through_raw(self):
        """A stale/hand-edited model_config.reasoning_effort outside
        {low, medium, high, xhigh} must never reach the generation call --
        stream_provider_backed_direct_chat would otherwise quote it verbatim
        into a system-prompt instruction for any model it doesn't recognize
        as natively reasoning-capable (see that function's degradation
        branch), which is a prompt-injection-shaped risk for a value that
        should have been rejected at the door."""
        spec = self._spec(provider="anthropic", model="claude-sonnet-4-6", mode="byok_api", reasoning_effort="ultra-mega")
        _mock_agent, _mock_ws, mock_stream = self._run_chat(specialist_context=spec)

        self.assertIsNone(mock_stream.call_args.kwargs["normalized_reasoning_effort"])

    def test_sage_own_turn_now_consults_its_own_master_reasoning_effort(self):
        """Phase 1 (reasoning-effort control): unlike model/provider (still
        specialist-only -- see _resolve_cloud_provider's docstring, a
        provider/credential switch), reasoning effort is a deliberate,
        scoped exception -- Sage's own turn now ALSO reads its own
        model_config.reasoning_effort, the field /thinking persists to
        (command_registry.py's _handle_thinking). Before this fix this was
        permanently unreachable from here, which is exactly why /thinking
        used to be inert."""
        master_install = {
            "id": "sage-main-1",
            "install_metadata": {"model_config": {"mode": "platform_credits", "reasoning_effort": "high"}},
        }
        with patch(
            "server_modules.agent_registry_repository.get_workspace_master_agent_install",
            new=AsyncMock(return_value=master_install),
        ):
            _mock_agent, _mock_ws, mock_stream = self._run_chat(specialist_context=None)

        self.assertEqual(mock_stream.call_args.kwargs["normalized_reasoning_effort"], "high")

    def test_sage_own_turn_with_no_resolvable_master_fails_safe_to_unset(self):
        """No master install found (or the lookup errors/times out) must
        never crash the turn -- just falls back to no override, same as an
        unconfigured agent."""
        with patch(
            "server_modules.agent_registry_repository.get_workspace_master_agent_install",
            new=AsyncMock(return_value=None),
        ):
            _mock_agent, _mock_ws, mock_stream = self._run_chat(specialist_context=None)

        self.assertIsNone(mock_stream.call_args.kwargs["normalized_reasoning_effort"])

    @staticmethod
    def _run_cli_subscription_chat(*, runtime, reasoning_effort):
        """Minimal harness for handle_sage_chat's cli_subscription branch --
        modeled on SageAgentRuntimeSpecialistProviderResolutionTests's own
        test_local_mode_specialist_never_touches_cloud_resolver (the
        structurally parallel "local" branch), since this branch dispatches
        entirely separately (gateway WSS rail) from the byok/platform_credits
        action-loop path the other _run_chat helper in this file covers."""
        spec = SpecialistRuntimeContext(
            agent_install_id="agent-cli-1", agent_label="CLI Agent", agent_kind="specialist",
            persona="You are a specialist.", mode="cli_subscription", runtime=runtime,
            gateway_binding="gw-1", reasoning_effort=reasoning_effort,
        )
        mock_dispatch = AsyncMock(return_value=("cli reply", {"input_tokens": 1, "output_tokens": 1}, "default"))
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.memory_service.get_memory", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", new=AsyncMock(return_value=("deepseek", {"api_key": "sk-workspace-default"}))),
            patch("server_modules.sage_agent_runtime_service._resolve_agent_cloud_provider", new=AsyncMock(side_effect=AssertionError("must not be called for cli_subscription"))),
            patch("server_modules.sage_agent_runtime_service._dispatch_cli_subscription_gateway_brain", new=mock_dispatch),
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
        ):
            _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="hello", specialist_context=spec,
            ))
        return mock_dispatch

    def test_cli_subscription_valid_reasoning_effort_reaches_the_gateway_dispatch(self):
        """"xhigh" is valid for BOTH claude_code and codex — proves the
        happy path threads through end-to-end via handle_sage_chat, not
        just the lower-level _dispatch_cli_subscription_gateway_brain unit
        tested directly above."""
        mock_dispatch = self._run_cli_subscription_chat(runtime="claude_code", reasoning_effort="xhigh")
        self.assertEqual(mock_dispatch.await_args.kwargs["reasoning_effort"], "xhigh")

    def test_cli_subscription_claude_code_drops_a_codex_only_value(self):
        """"off" is real for codex's -c model_reasoning_effort= but NOT for
        the Claude CLI's --effort (verified live against each CLI's own
        --help — see _VALID_CLI_REASONING_EFFORTS_BY_RUNTIME's docstring). A
        stale value saved under a different runtime must never reach the
        wrong CLI's flag."""
        mock_dispatch = self._run_cli_subscription_chat(runtime="claude_code", reasoning_effort="off")
        self.assertEqual(mock_dispatch.await_args.kwargs["reasoning_effort"], "")

    def test_cli_subscription_codex_accepts_its_own_off_value(self):
        """The SAME "off" value IS valid for codex — proves the two
        runtimes' vocabularies are validated independently, not against one
        shared/lowest-common-denominator set."""
        mock_dispatch = self._run_cli_subscription_chat(runtime="codex", reasoning_effort="off")
        self.assertEqual(mock_dispatch.await_args.kwargs["reasoning_effort"], "off")

    def test_sage_own_turn_invalid_master_reasoning_effort_is_dropped(self):
        """Same _VALID_REASONING_EFFORTS gate applies to Sage's own turn as
        to a specialist's -- a stale/hand-edited value (or one saved for a
        cli_subscription runtime's wider vocabulary, e.g. "max") never
        reaches the generation call raw."""
        master_install = {
            "id": "sage-main-1",
            "install_metadata": {"model_config": {"mode": "platform_credits", "reasoning_effort": "max"}},
        }
        with patch(
            "server_modules.agent_registry_repository.get_workspace_master_agent_install",
            new=AsyncMock(return_value=master_install),
        ):
            _mock_agent, _mock_ws, mock_stream = self._run_chat(specialist_context=None)

        self.assertIsNone(mock_stream.call_args.kwargs["normalized_reasoning_effort"])


class SageAgentRuntimeEngineSelectionResolutionTests(unittest.TestCase):
    """MAN-310 Phase 1: model_config.engine ("legacy" | "claude_agent_sdk")
    must reach the _run_sage_action_loop_v3 turn-engine seam as
    engine_options={"engine": claude_agent_sdk_bridge.ENGINE_ID} -- proven
    by checking WHICH generation entry point _collect_stream_events
    actually calls (claude_agent_sdk_bridge.collect_events_via_claude_
    agent_sdk vs. the legacy direct_chat_generation_service.stream_
    provider_backed_direct_chat), the same way
    SageAgentRuntimeReasoningEffortResolutionTests just above proves
    reasoning_effort reaches its own kwarg -- a resolved local variable
    alone wouldn't prove the branch this phase's whole safety property
    (flag off == legacy, byte for byte) actually depends on. Defines its
    own harness (not SageAgentRuntimeSpecialistProviderResolutionTests's
    _run_chat) because that helper doesn't patch the bridge function."""

    _spec = staticmethod(SageAgentRuntimeSpecialistProviderResolutionTests._spec)

    @staticmethod
    def _run_chat(specialist_context=None, mock_agent_provider=None, engine_options=None):
        mock_agent_provider = mock_agent_provider or AsyncMock(
            return_value=("anthropic", {"api_key": "sk-agent-own-key"}, "byok_api")
        )
        mock_workspace_provider = AsyncMock(return_value=("deepseek", {"api_key": "sk-workspace-default"}))
        bridge_events = [{"type": "final", "payload": {"reply": "SDK reply", "error": None}}]
        with (
            patch(
                "server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile",
                return_value={"profile": {"user_name": "", "identity_summary": "", "communication_style": "", "recurring_responsibility": "", "standing_rules": []}},
            ),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.memory_service.get_memory", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", new=mock_workspace_provider),
            patch("server_modules.sage_agent_runtime_service._resolve_agent_cloud_provider", new=mock_agent_provider),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability",
                return_value={"runtime_ok": True, "local_gateway_online": True},
            ),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat",
            ) as mock_stream,
            patch(
                "server_modules.claude_agent_sdk_bridge.collect_events_via_claude_agent_sdk",
                return_value=bridge_events,
            ) as mock_bridge,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            mock_stream.return_value = iter([{
                "type": "final",
                "payload": {"reply": "Legacy reply", "actions": [], "error": None},
            }])
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="hello",
                specialist_context=specialist_context,
                engine_options=engine_options,
            ))
        return result, mock_stream, mock_bridge

    def test_byok_specialist_with_sdk_engine_calls_the_bridge_not_the_legacy_stream(self):
        spec = self._spec(provider="anthropic", model="claude-sonnet-4-6", mode="byok_api", engine="claude_agent_sdk")
        result, mock_stream, mock_bridge = self._run_chat(specialist_context=spec)

        mock_bridge.assert_called_once()
        mock_stream.assert_not_called()
        self.assertEqual(result["message"], "SDK reply")

    def test_byok_specialist_with_unset_engine_under_pytest_shortcut_stays_on_legacy_stream(self):
        """NOT a production guarantee — this is the PYTEST_CURRENT_TEST
        shortcut's own effect (_resolve_turn_engine_id returns "" under
        pytest specifically so pre-existing mocks like this one keep
        working), and it was previously mislabeled as one. Before MAN-310
        (c96c7138a, "Claude Agent SDK is now the default turn engine") an
        agent with no engine configured really did stay on the legacy
        stream in production too, and this test's old name/docstring
        ("The safety property this whole phase must not break... must call
        the EXACT SAME legacy entry point as always") was accurate then.
        It has been false in production since that commit — this test
        stayed green only because it always runs under pytest, which is
        exactly the blind spot CLAUDE.md's own MAN-310 entry warns about:
        "every test that does not pass engine_options exercises the engine
        production does NOT use." See the companion test immediately below
        for what an unset engine actually selects outside pytest."""
        spec = self._spec(provider="anthropic", model="claude-sonnet-4-6", mode="byok_api")
        result, mock_stream, mock_bridge = self._run_chat(specialist_context=spec)

        mock_stream.assert_called_once()
        mock_bridge.assert_not_called()
        self.assertEqual(result["message"], "Legacy reply")

    def test_byok_specialist_with_unset_engine_uses_the_sdk_bridge_in_real_production(self):
        """The real default, outside the pytest shortcut the test above
        documents. Same specialist config (no model_config.engine at all)
        as the test above — the ONLY difference is PYTEST_CURRENT_TEST
        being unset for the duration of this one call, the same technique
        test_default_engine_credit_debit.py's own
        test_production_default_engine_is_the_claude_agent_sdk uses to
        pin _resolve_turn_engine_id's real default. This is the actual
        behavior every one of this codebase's real customers gets today
        for an agent that has never touched model_config.engine."""
        spec = self._spec(provider="anthropic", model="claude-sonnet-4-6", mode="byok_api")
        with patch.dict(os.environ, {}, clear=False):
            saved_pytest_marker = os.environ.pop("PYTEST_CURRENT_TEST", None)
            try:
                result, mock_stream, mock_bridge = self._run_chat(specialist_context=spec)
            finally:
                if saved_pytest_marker is not None:
                    os.environ["PYTEST_CURRENT_TEST"] = saved_pytest_marker

        mock_bridge.assert_called_once()
        mock_stream.assert_not_called()
        self.assertEqual(result["message"], "SDK reply")

    def test_byok_specialist_with_literal_legacy_engine_stays_on_the_legacy_stream(self):
        spec = self._spec(provider="anthropic", model="claude-sonnet-4-6", mode="byok_api", engine="legacy")
        result, mock_stream, mock_bridge = self._run_chat(specialist_context=spec)

        mock_stream.assert_called_once()
        mock_bridge.assert_not_called()

    def test_byok_specialist_with_garbage_engine_value_fails_safe_to_legacy(self):
        """A stale/hand-edited model_config.engine outside {legacy,
        claude_agent_sdk} must never select the bridge -- same fail-safe
        convention as reasoning_effort's own invalid-value handling."""
        spec = self._spec(provider="anthropic", model="claude-sonnet-4-6", mode="byok_api", engine="some-future-engine")
        result, mock_stream, mock_bridge = self._run_chat(specialist_context=spec)

        mock_stream.assert_called_once()
        mock_bridge.assert_not_called()

    def test_platform_credits_specialist_with_sdk_engine_calls_the_bridge(self):
        """engine is meaningful for platform_credits too, not just byok_api
        -- both reach the same turn-engine seam."""
        spec = self._spec(provider="", model="", mode="platform_credits", engine="claude_agent_sdk")
        mock_agent_provider = AsyncMock(return_value=("deepseek", {"api_key": "sk-workspace-default"}, "platform_credits"))
        result, mock_stream, mock_bridge = self._run_chat(specialist_context=spec, mock_agent_provider=mock_agent_provider)

        mock_bridge.assert_called_once()
        mock_stream.assert_not_called()

    def test_sage_own_turn_consults_its_own_master_engine(self):
        """Sage's own (master) turn also resolves model_config.engine off
        its own install -- the same specialist-vs-master resolution
        reasoning_effort already gets (see
        test_sage_own_turn_now_consults_its_own_master_reasoning_effort
        above)."""
        master_install = {
            "id": "sage-main-1",
            "install_metadata": {"model_config": {"mode": "platform_credits", "engine": "claude_agent_sdk"}},
        }
        with patch(
            "server_modules.agent_registry_repository.get_workspace_master_agent_install",
            new=AsyncMock(return_value=master_install),
        ):
            result, mock_stream, mock_bridge = self._run_chat(specialist_context=None)

        mock_bridge.assert_called_once()
        mock_stream.assert_not_called()
        self.assertEqual(result["message"], "SDK reply")

    def test_sage_own_turn_with_no_master_engine_stays_on_legacy(self):
        master_install = {
            "id": "sage-main-1",
            "install_metadata": {"model_config": {"mode": "platform_credits"}},
        }
        with patch(
            "server_modules.agent_registry_repository.get_workspace_master_agent_install",
            new=AsyncMock(return_value=master_install),
        ):
            result, mock_stream, mock_bridge = self._run_chat(specialist_context=None)

        mock_stream.assert_called_once()
        mock_bridge.assert_not_called()

    def test_explicit_caller_supplied_engine_options_wins_over_persisted_config(self):
        """handle_sage_chat's own engine_options parameter (the pre-existing
        explicit override -- see its docstring) still wins over the
        resolved per-agent model_config.engine when a caller supplies it,
        exactly as documented -- proven here with the two in direct
        conflict: persisted config says the SDK engine, the explicit
        caller override says legacy."""
        spec = self._spec(provider="anthropic", model="claude-sonnet-4-6", mode="byok_api", engine="claude_agent_sdk")
        result, mock_stream, mock_bridge = self._run_chat(
            specialist_context=spec, engine_options={"engine": "legacy"},
        )

        mock_stream.assert_called_once()
        mock_bridge.assert_not_called()

    def test_explicit_caller_supplied_engine_options_can_opt_in_even_when_persisted_config_is_unset(self):
        spec = self._spec(provider="anthropic", model="claude-sonnet-4-6", mode="byok_api")
        result, mock_stream, mock_bridge = self._run_chat(
            specialist_context=spec, engine_options={"engine": "claude_agent_sdk"},
        )

        mock_bridge.assert_called_once()
        mock_stream.assert_not_called()


class EngineAwareCapabilityManifestIntegrationTests(unittest.TestCase):
    """fix/agent-task-tools-on-sdk-engine, end to end: handle_sage_chat must
    build a DIFFERENT system_prompt depending on which engine the turn
    actually runs on -- the "## Callable Tools" manifest and kernel text
    must not promise a Tier-2-only tool / query_tool_registry on the SDK
    engine (no discovery door there at all -- claude_agent_sdk_bridge.
    _UNSUPPORTED_TOOL_NAMES), while the legacy engine keeps today's
    behavior byte-for-byte. Proves the actual _tool_discovery_available
    plumbing added to handle_sage_chat, not just the compiler unit in
    isolation (see test_sage_instruction_compiler_service.py for that)."""

    @staticmethod
    def _run_chat(*, engine_options=None):
        mock_workspace_provider = AsyncMock(return_value=("deepseek", {"api_key": "sk-workspace-default"}))
        bridge_events = [{"type": "final", "payload": {"reply": "SDK reply", "error": None}}]
        capability_items = [
            # Not native this turn (no core/fleet/subagent schema) -- only
            # ever reachable via a query_tool_registry pull on the legacy
            # engine.
            {"tool_id": "slack__post_message", "label": "Post to Slack", "description": "Send a message.", "status": "ready", "type": "tool"},
        ]
        with (
            patch(
                "server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile",
                return_value={"profile": {"user_name": "", "identity_summary": "", "communication_style": "", "recurring_responsibility": "", "standing_rules": []}},
            ),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.memory_service.get_memory", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", new=mock_workspace_provider),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability",
                return_value={"runtime_ok": True, "local_gateway_online": True},
            ),
            patch(
                "server_modules.sage_instruction_compiler_service.sage_skills_api.build_sage_capabilities_payload",
                return_value={"items": capability_items},
            ),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat",
            ) as mock_stream,
            patch(
                "server_modules.claude_agent_sdk_bridge.collect_events_via_claude_agent_sdk",
                return_value=bridge_events,
            ) as mock_bridge,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            mock_stream.return_value = iter([{
                "type": "final",
                "payload": {"reply": "Legacy reply", "actions": [], "error": None},
            }])
            _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="hello",
                specialist_context=None,
                engine_options=engine_options,
            ))
        return mock_stream, mock_bridge

    def test_legacy_engine_manifest_keeps_todays_behavior(self):
        mock_stream, mock_bridge = self._run_chat(engine_options={"engine": "legacy"})
        mock_bridge.assert_not_called()
        system_prompt = mock_stream.call_args.kwargs["system_prompt"]
        self.assertIn("## Callable Tools", system_prompt)
        self.assertIn("slack__post_message", system_prompt)
        self.assertIn("query_tool_registry finds one", system_prompt)

    def test_sdk_engine_manifest_hides_the_unreachable_tool_and_the_dead_instruction(self):
        mock_stream, mock_bridge = self._run_chat(engine_options={"engine": "claude_agent_sdk"})
        mock_stream.assert_not_called()
        system_prompt = mock_bridge.call_args.kwargs["system_prompt"]
        self.assertIn("## Callable Tools", system_prompt)
        self.assertNotIn("slack__post_message", system_prompt)
        self.assertNotIn("query_tool_registry", system_prompt)
        self.assertIn("not reachable this turn", system_prompt)


class SageAgentRuntimeSpecialistMemoryLoadTests(unittest.TestCase):
    """The core fix under test: a specialist's turn must load ITS OWN
    MEMORY.md index into its system prompt every turn, the same way Sage
    loads its own root MEMORY.md (via build_root_memory_brief_sections) —
    and never Sage's or another specialist's.

    Before this fix, the specialist branch built its "## Your memory" block
    from memory_service.get_memory(agent_install_id=...) — agent_memory.py's
    SQLite `memory_entries` table. Nothing on any live turn ever wrote to
    that table with a real agent_install_id (save_memory/delete_memory's
    only live callers are workspace-root-scoped: the /forget slash command,
    store_direct_chat_memory_fact, handle_no_provider_memory_request), so
    the read was always empty and every specialist turn ran with no memory
    index at all — MEMORY.md itself was never read for a specialist turn.
    """

    @staticmethod
    def _spec(agent_install_id="agent-a", **overrides):
        base = dict(
            agent_install_id=agent_install_id,
            agent_label="Research Agent",
            agent_kind="specialist",
            persona="You are a research specialist.",
        )
        base.update(overrides)
        return SpecialistRuntimeContext(**base)

    @staticmethod
    def _run_chat(*, specialist_context, files_by_install):
        """files_by_install maps agent_install_id (or None for Sage's own
        root-level call) to the context-files dict
        workspace_context.read_workspace_context_files should return for
        that install — proving the specialist branch requests ITS OWN
        install's namespace, never the workspace root's or a sibling's."""
        def _fake_read_workspace_context_files(*, workspace_id=None, agent_install_id=None):
            return dict(files_by_install.get(agent_install_id, {}))

        mock_agent_provider = AsyncMock(
            return_value=("anthropic", {"api_key": "sk-agent-own-key"}, "byok_api")
        )
        mock_workspace_provider = AsyncMock(
            return_value=("deepseek", {"api_key": "sk-workspace-default"})
        )
        stream_events = [{
            "type": "final",
            "payload": {"reply": "Reply", "actions": [], "error": None},
        }]
        with (
            patch(
                "server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile",
                return_value={"profile": {"user_name": "", "identity_summary": "", "communication_style": "", "recurring_responsibility": "", "standing_rules": []}},
            ),
            patch(
                "server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files",
                side_effect=_fake_read_workspace_context_files,
            ),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            # The dead SQLite read this fix removes from the specialist
            # branch — proven gone by making it explode if ever called,
            # not just by checking a return value.
            patch(
                "server_modules.memory_service.get_memory",
                side_effect=AssertionError(
                    "dead SQLite memory_entries read must not be called for a specialist turn"
                ),
            ),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", new=mock_workspace_provider),
            patch("server_modules.sage_agent_runtime_service._resolve_agent_cloud_provider", new=mock_agent_provider),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability",
                return_value={"runtime_ok": True, "local_gateway_online": True},
            ),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat",
            ) as mock_stream,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            mock_stream.return_value = iter(stream_events)
            _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="hello",
                specialist_context=specialist_context,
            ))
        return mock_stream

    def test_specialist_turn_includes_its_own_memory_md_index(self):
        spec = self._spec(agent_install_id="agent-a")
        files_by_install = {
            "agent-a": {"MEMORY.md": "## Summary\n\nAgent A tracks the apollo-project deadline (Friday).\n"},
        }
        mock_stream = self._run_chat(specialist_context=spec, files_by_install=files_by_install)

        system_prompt = mock_stream.call_args.kwargs["system_prompt"]
        self.assertIn("apollo-project", system_prompt)
        self.assertIn("MEMORY.md (Agent Memory Index)", system_prompt)
        self.assertIn("## Your memory", system_prompt)

    def test_specialist_turn_never_sees_sages_root_memory(self):
        spec = self._spec(agent_install_id="agent-a")
        files_by_install = {
            "agent-a": {"MEMORY.md": "## Summary\n\nAgent A's own fact: nightingale.\n"},
            # None == Sage's own root-level call (no agent_install_id).
            None: {"MEMORY.md": "## Summary\n\nSage's own root fact: workspace owner is Mansur, firefly.\n"},
        }
        mock_stream = self._run_chat(specialist_context=spec, files_by_install=files_by_install)

        system_prompt = mock_stream.call_args.kwargs["system_prompt"]
        self.assertIn("nightingale", system_prompt)
        self.assertNotIn("firefly", system_prompt)
        self.assertNotIn("workspace owner is Mansur", system_prompt)

    def test_specialist_turn_never_sees_another_specialists_memory(self):
        spec = self._spec(agent_install_id="agent-a")
        files_by_install = {
            "agent-a": {"MEMORY.md": "## Summary\n\nAgent A's own fact: nightingale.\n"},
            "agent-b": {"MEMORY.md": "## Summary\n\nAgent B's private fact: zeta-merger financial terms.\n"},
        }
        mock_stream = self._run_chat(specialist_context=spec, files_by_install=files_by_install)

        system_prompt = mock_stream.call_args.kwargs["system_prompt"]
        self.assertIn("nightingale", system_prompt)
        self.assertNotIn("zeta-merger", system_prompt)

    def test_two_specialists_each_see_only_their_own_memory(self):
        """The task's literal verify ask, mirrored from the sibling
        provider-resolution test class: two agents, two different memory
        files, each turn proven to load the right one and only the right
        one."""
        files_by_install = {
            "agent-a": {"MEMORY.md": "## Summary\n\nAgent A fact: projectnightingale.\n"},
            "agent-b": {"MEMORY.md": "## Summary\n\nAgent B fact: projectfirefly.\n"},
        }
        stream_a = self._run_chat(specialist_context=self._spec(agent_install_id="agent-a"), files_by_install=files_by_install)
        prompt_a = stream_a.call_args.kwargs["system_prompt"]
        self.assertIn("projectnightingale", prompt_a)
        self.assertNotIn("projectfirefly", prompt_a)

        stream_b = self._run_chat(specialist_context=self._spec(agent_install_id="agent-b"), files_by_install=files_by_install)
        prompt_b = stream_b.call_args.kwargs["system_prompt"]
        self.assertIn("projectfirefly", prompt_b)
        self.assertNotIn("projectnightingale", prompt_b)

    def test_fresh_specialist_with_untouched_memory_md_gets_empty_index_not_fabricated_content(self):
        """A brand-new agent's MEMORY.md is still the default scaffold — the
        turn must reflect an empty index, never invented/seeded memory
        content. The scaffold's own boilerplate text must not leak into the
        prompt disguised as a real memory fact."""
        spec = self._spec(agent_install_id="agent-fresh")
        files_by_install = {
            "agent-fresh": {"MEMORY.md": sage_agent_runtime_service.workspace_context.DEFAULT_CONTEXT_FILE_CONTENTS["MEMORY.md"]},
        }
        mock_stream = self._run_chat(specialist_context=spec, files_by_install=files_by_install)

        system_prompt = mock_stream.call_args.kwargs["system_prompt"]
        self.assertNotIn("## Your memory", system_prompt)
        self.assertNotIn("Agent Memory Index", system_prompt)

    def test_specialist_with_no_context_files_at_all_gets_empty_index(self):
        """No files_by_install entry for this install (as if the namespace
        were brand new and read_workspace_context_files returned {}) — must
        degrade to an empty index, not raise or fabricate."""
        spec = self._spec(agent_install_id="agent-nothing-yet")
        mock_stream = self._run_chat(specialist_context=spec, files_by_install={})

        system_prompt = mock_stream.call_args.kwargs["system_prompt"]
        self.assertNotIn("## Your memory", system_prompt)


class SageAgentRuntimeSpecialistCapabilityManifestTests(unittest.TestCase):
    """docs/design/context-engineering-plan.md item 10 (doctrine rewrite part
    2): a specialist's turn used to include NO capability manifest at all —
    the audit's sharpest doctrine gap and a plausible root cause of the
    founder's mid-task hallucination complaint. This proves the fix: a
    specialist now gets a "## Callable Tools" manifest, scoped to what THAT
    install can actually call via the SAME per-install tool scoping the
    native tool list already uses (_specialist_tool_allowed) — never an
    unscoped copy of the workspace-wide manifest."""

    @staticmethod
    def _spec(agent_install_id="agent-a", **overrides):
        base = dict(
            agent_install_id=agent_install_id,
            agent_label="Research Agent",
            agent_kind="specialist",
            persona="You are a research specialist.",
        )
        base.update(overrides)
        return SpecialistRuntimeContext(**base)

    @staticmethod
    def _run_chat(*, specialist_context, capability_items, toolset):
        mock_agent_provider = AsyncMock(
            return_value=("anthropic", {"api_key": "sk-agent-own-key"}, "byok_api")
        )
        mock_workspace_provider = AsyncMock(
            return_value=("deepseek", {"api_key": "sk-workspace-default"})
        )
        stream_events = [{
            "type": "final",
            "payload": {"reply": "Reply", "actions": [], "error": None},
        }]
        with (
            patch(
                "server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile",
                return_value={"profile": {"user_name": "", "identity_summary": "", "communication_style": "", "recurring_responsibility": "", "standing_rules": []}},
            ),
            patch(
                "server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files",
                return_value={},
            ),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", new=mock_workspace_provider),
            patch("server_modules.sage_agent_runtime_service._resolve_agent_cloud_provider", new=mock_agent_provider),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability",
                return_value={"runtime_ok": True, "local_gateway_online": True},
            ),
            patch(
                "server_modules.sage_instruction_compiler_service.sage_skills_api.build_sage_capabilities_payload",
                return_value={"items": capability_items},
            ),
            patch(
                "server_modules.sage_agent_runtime_service._resolve_specialist_toolset",
                new=AsyncMock(return_value=toolset),
            ),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat",
            ) as mock_stream,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            mock_stream.return_value = iter(stream_events)
            _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="hello",
                specialist_context=specialist_context,
            ))
        return mock_stream

    def test_specialist_now_receives_a_capability_manifest_at_all(self):
        """Before item 10, this header never appeared anywhere in a
        specialist's system prompt, regardless of what was bound."""
        capability_items = [
            {"tool_id": "web__search", "label": "Web search", "description": "Search the web.", "status": "ready", "type": "tool"},
        ]
        toolset = {
            "core": {"task_complete", "query_tool_registry", "update_plan", "web__search"},
            "connectors": set(), "tools": set(), "raw_tool_toggles": {},
            "mandate_audience_tools": [], "capability_providers": frozenset(),
        }
        mock_stream = self._run_chat(
            specialist_context=self._spec(), capability_items=capability_items, toolset=toolset,
        )
        system_prompt = mock_stream.call_args.kwargs["system_prompt"]
        self.assertIn("## Callable Tools", system_prompt)

    def test_specialist_manifest_is_scoped_not_the_full_workspace_list(self):
        capability_items = [
            {"tool_id": "web__search", "label": "Web search", "description": "Search the web.", "status": "ready", "type": "tool"},
            # Operator-only fleet tool: never in a specialist's core/tools/connectors.
            {"tool_id": "fleet__create_agent", "label": "Create Agent", "description": "Create a new specialist agent in the workspace.", "status": "ready", "type": "tool"},
            # Capability-gated (image_generation); no resolved provider for this agent.
            {"tool_id": "generate_image", "label": "Generate image", "description": "Generate one or more images from a prompt and save them locally.", "status": "ready", "type": "tool"},
            # A workspace-authored skill this specialist IS bound to (skill_invoke enabled).
            {"tool_id": "skill_invoke", "label": "acme-quote-builder", "description": "Builds a customer quote from the workspace price list. Call skill_invoke with skill_id=\"acme-quote-builder\" to run it.", "status": "ready", "type": "skill", "source": "workspace"},
        ]
        toolset = {
            "core": {"task_complete", "query_tool_registry", "update_plan", "web__search", "web__fetch", "memory_write", "memory_read", "memory_search", "memory_get", "memory_append_daily_note", "hardware__action"},
            "connectors": set(),  # "fleet" NOT bound
            "tools": {"skill_invoke"},  # explicitly enabled for this install
            "raw_tool_toggles": {},
            "mandate_audience_tools": [],
            "capability_providers": frozenset(),  # image_generation NOT resolved
        }
        mock_stream = self._run_chat(
            specialist_context=self._spec(), capability_items=capability_items, toolset=toolset,
        )
        system_prompt = mock_stream.call_args.kwargs["system_prompt"]

        self.assertIn("## Callable Tools", system_prompt)
        # Allowed + native (item 4 dedup): name-only mention, not a full
        # re-described line, but still present so the model knows it's live.
        self.assertIn("web__search", system_prompt)
        # Never bound for this specialist — must not leak into its manifest.
        self.assertNotIn("fleet__create_agent", system_prompt)
        self.assertNotIn("generate_image", system_prompt)
        # Allowed + manifest-only (no native schema): full description line,
        # un-truncated (item 4) since this is skill_invoke's only channel.
        self.assertIn("acme-quote-builder", system_prompt)
        self.assertIn("Builds a customer quote from the workspace price list", system_prompt)

    def test_specialist_with_no_bound_tools_gets_no_manifest_leak(self):
        """An install bound to nothing beyond bare core tools must not see
        anything it can't call — the manifest degrades to native-only (or
        nothing) rather than ever showing an unscoped workspace-wide list."""
        capability_items = [
            {"tool_id": "fleet__list_agents", "label": "List Agents", "description": "List all agents in the workspace.", "status": "ready", "type": "tool"},
            {"tool_id": "skill_invoke", "label": "some-other-skill", "description": "Some other workspace skill.", "status": "ready", "type": "skill", "source": "workspace"},
        ]
        toolset = {
            "core": {"task_complete", "query_tool_registry", "update_plan"},
            "connectors": set(), "tools": set(), "raw_tool_toggles": {},
            "mandate_audience_tools": [], "capability_providers": frozenset(),
        }
        mock_stream = self._run_chat(
            specialist_context=self._spec(), capability_items=capability_items, toolset=toolset,
        )
        system_prompt = mock_stream.call_args.kwargs["system_prompt"]
        self.assertNotIn("fleet__list_agents", system_prompt)
        self.assertNotIn("some-other-skill", system_prompt)


# ── 2026-07-24 compaction end-to-end fix pass: BUG 1 (placement + swallow),
# BUG 5 (falsy-zero max_context_tokens), BUG 6 (model downgrade) ───────────

class PostTurnAutoCompactionPlacementTests(unittest.TestCase):
    """BUG 1 root cause: _schedule_post_turn_auto_compaction (the extracted
    B1 background-compaction dispatch) used to be defined ONLY after the
    action-loop-success `if action_result is not None: ... return {...}`
    block — a branch _run_sage_action_loop_v3's own call site comment says
    "always runs" and which returns non-None for every turn except total
    failure. Live-repro-confirmed: instrumentation on the old inline block
    never fired on a normal tool-using turn. This proves the fix: the call
    now fires from BOTH of handle_sage_chat's exit points."""

    def _run_with_mocked_schedule(self, *, stream_events):
        schedule_mock = MagicMock()
        with (
            patch(
                "server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile",
                return_value={"profile": {"user_name": "", "identity_summary": "", "communication_style": "", "recurring_responsibility": "", "standing_rules": []}},
            ),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.memory_service.get_memory", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", return_value=("deepseek", {"api_key": "test"})),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability",
                return_value={"runtime_ok": True, "local_gateway_online": True},
            ),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat",
            ) as mock_stream,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
            patch.object(sage_agent_runtime_service, "_schedule_post_turn_auto_compaction", new=schedule_mock),
        ):
            mock_stream.return_value = iter(stream_events)
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="hello",
            ))
        return result, schedule_mock

    def test_fires_on_the_action_loop_success_path_not_just_the_fallback(self):
        # A normal tool-using turn: the action loop produces a real reply.
        # This is the "always runs" common case the old inline B1 block was
        # structurally unreachable from.
        stream_events = [{
            "type": "final",
            "payload": {"reply": "Here you go.", "actions": [], "error": None},
        }]
        result, schedule_mock = self._run_with_mocked_schedule(stream_events=stream_events)

        self.assertEqual(result["message"], "Here you go.")
        schedule_mock.assert_called_once()
        call_kwargs = schedule_mock.call_args.kwargs
        self.assertEqual(call_kwargs["workspace_id"], "ws-1")
        self.assertEqual(call_kwargs["provider"], "deepseek")


class PostTurnAutoCompactionExceptionLoggingTests(unittest.TestCase):
    """BUG 1's second root cause: the old inline background job wrapped its
    entire body in `except Exception: pass` with zero logging. Any failure
    on the rare turns that DID reach it was invisible. Now logged with full
    context AND recorded as a durable security-audit event."""

    def test_failure_inside_the_background_job_is_logged_not_swallowed(self):
        with (
            patch(
                "server_modules.sage_agent_runtime_service.thread_service.get_thread",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
            patch(
                "server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event",
            ) as mock_audit,
            self.assertLogs("server_modules.sage_agent_runtime_service", level="ERROR") as log_ctx,
        ):
            _run(sage_agent_runtime_service._run_post_turn_auto_compaction(
                workspace_id="ws-1", tenant_id="default", thread_id="sage-main",
                provider="deepseek", model="deepseek-chat",
                ctx_policy_max=0, ctx_policy_action="compact",
                session_id="sess-1", trace_id="trace-1",
            ))

        self.assertTrue(any("FAILED" in m for m in log_ctx.output))
        self.assertTrue(any("boom" in m for m in log_ctx.output))
        mock_audit.assert_called_once()
        self.assertEqual(mock_audit.call_args.kwargs["action"], "compaction.background_job_failed")
        self.assertEqual(mock_audit.call_args.kwargs["status"], "failure")

    def test_failed_schedule_itself_is_logged(self):
        # If even scheduling the fire-and-forget task blows up (e.g.
        # asyncio.ensure_future itself raising), that must be logged too —
        # not just failures inside the task body. _run_post_turn_auto_
        # compaction is mocked to a plain MagicMock (not AsyncMock) so
        # calling it here doesn't create a real coroutine object that would
        # otherwise be left dangling (unawaited, uncancelled) once
        # ensure_future raises before ever consuming it.
        with (
            patch.object(
                sage_agent_runtime_service, "_run_post_turn_auto_compaction", new=MagicMock(),
            ),
            patch("asyncio.ensure_future", side_effect=RuntimeError("no loop")),
            self.assertLogs("server_modules.sage_agent_runtime_service", level="ERROR") as log_ctx,
        ):
            sage_agent_runtime_service._schedule_post_turn_auto_compaction(
                workspace_id="ws-1", tenant_id="default", thread_id="sage-main",
                provider="deepseek", model="deepseek-chat",
                ctx_policy_max=0, ctx_policy_action="compact",
                session_id="sess-1", trace_id="trace-1",
            )
        self.assertTrue(any("failed to SCHEDULE" in m for m in log_ctx.output))


class PostTurnAutoCompactionTaskReferenceTests(unittest.IsolatedAsyncioTestCase):
    """MAN-266: _schedule_post_turn_auto_compaction used to call
    asyncio.ensure_future(_run_post_turn_auto_compaction(...)) as a bare
    statement, discarding the returned Task immediately. The event loop
    only holds a WEAK reference to a Task (see asyncio.create_task's own
    "Important: Save a reference to the result" docs note); with nothing
    else keeping it alive, it could be garbage-collected while still
    pending -- matching the recurring production
    'ERROR [asyncio] Task was destroyed but it is pending!' log line this
    ticket reports. server_modules/tests/test_exception_and_task_lint.py
    independently confirms this was the file's ONLY unassigned
    create_task/ensure_future site (it's been removed from that lint
    test's baseline as part of this fix).

    Fixed by tracking the Task in the module-level
    _POST_TURN_COMPACTION_TASKS set with a done-callback that discards it
    on completion (same pattern as routes_gateway.py's
    _VPS_PROVISION_BACKGROUND_TASKS). This test proves both halves: the
    task is tracked while running, and untracked once it finishes."""

    async def test_scheduled_task_is_tracked_while_pending_and_untracked_on_completion(self):
        release = asyncio.Event()

        async def _fake_job(**kwargs):
            await release.wait()

        with patch.object(sage_agent_runtime_service, "_run_post_turn_auto_compaction", new=_fake_job):
            sage_agent_runtime_service._schedule_post_turn_auto_compaction(
                workspace_id="ws-1", tenant_id="default", thread_id="sage-main",
                provider="deepseek", model="deepseek-chat",
                ctx_policy_max=0, ctx_policy_action="compact",
                session_id="sess-1", trace_id="trace-1",
            )
            # ensure_future only SCHEDULES the task -- it needs one trip
            # through the loop before it's actually running and has had a
            # chance to register itself.
            await asyncio.sleep(0)

            self.assertEqual(
                len(sage_agent_runtime_service._POST_TURN_COMPACTION_TASKS), 1,
                "scheduled task must be held by a strong reference while pending",
            )
            tracked_task = next(iter(sage_agent_runtime_service._POST_TURN_COMPACTION_TASKS))
            self.assertFalse(tracked_task.done())

            release.set()
            await tracked_task

        self.assertEqual(
            len(sage_agent_runtime_service._POST_TURN_COMPACTION_TASKS), 0,
            "the done-callback must discard the task once it completes, so this never grows unbounded",
        )


class ContextPolicyFalsyZeroTests(unittest.TestCase):
    """BUG 5 falsy-zero: capability_presets.PRESET_STANDARD and
    PRESET_OPERATOR (the two most common agent presets) both set
    `context_policy: {"max_context_tokens": 0, ...}  # 0 = use model
    default`. The old `.get("max_context_tokens") or _DEFAULT_CTX_POLICY_
    MAX` silently coerced that explicit 0 into the 128K safety default,
    clamping every Standard/Operator-preset agent to 128K regardless of its
    real model window."""

    def test_explicit_zero_max_context_tokens_is_not_promoted_to_the_128k_default(self):
        spec = SpecialistRuntimeContext(
            agent_install_id="agent-1",
            agent_label="Standard Agent",
            agent_kind="specialist",
            persona="You are a helpful agent.",
            provider="anthropic",
            model="claude-opus-4-8",
            context_policy={"max_context_tokens": 0, "on_context_full": "compact"},
        )
        schedule_mock = MagicMock()
        stream_events = [{
            "type": "final",
            "payload": {"reply": "Reply.", "actions": [], "error": None},
        }]
        with (
            patch(
                "server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile",
                return_value={"profile": {"user_name": "", "identity_summary": "", "communication_style": "", "recurring_responsibility": "", "standing_rules": []}},
            ),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.memory_service.get_memory", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch(
                "server_modules.sage_agent_runtime_service._resolve_agent_cloud_provider",
                new=AsyncMock(return_value=("anthropic", {"api_key": "sk-agent-key"}, "platform_credits")),
            ),
            patch(
                "server_modules.sage_agent_runtime_service._resolve_cloud_provider",
                return_value=("anthropic", {"api_key": "sk-agent-key"}),
            ),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability",
                return_value={"runtime_ok": True, "local_gateway_online": True},
            ),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat",
            ) as mock_stream,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
            patch.object(sage_agent_runtime_service, "_schedule_post_turn_auto_compaction", new=schedule_mock),
        ):
            mock_stream.return_value = iter(stream_events)
            _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="hello",
                specialist_context=spec,
            ))

        schedule_mock.assert_called_once()
        # The fix: ctx_policy_max must be exactly 0 (an explicit "no
        # per-install clamp — trust the model's real window"), never
        # silently promoted to compaction_service.DEFAULT_CONTEXT_WINDOW
        # (128000).
        self.assertEqual(schedule_mock.call_args.kwargs["ctx_policy_max"], 0)
        self.assertNotEqual(
            schedule_mock.call_args.kwargs["ctx_policy_max"],
            compaction_service.DEFAULT_CONTEXT_WINDOW,
        )

    def test_unset_context_policy_still_falls_back_to_the_safety_default(self):
        # No context_policy at all (the field defaults to {}) must still
        # land on the 128K safety default — only an EXPLICIT 0 is special.
        spec = SpecialistRuntimeContext(
            agent_install_id="agent-2",
            agent_label="Bare Agent",
            agent_kind="specialist",
            persona="You are a helpful agent.",
            provider="anthropic",
            model="claude-opus-4-8",
        )
        schedule_mock = MagicMock()
        stream_events = [{
            "type": "final",
            "payload": {"reply": "Reply.", "actions": [], "error": None},
        }]
        with (
            patch(
                "server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile",
                return_value={"profile": {"user_name": "", "identity_summary": "", "communication_style": "", "recurring_responsibility": "", "standing_rules": []}},
            ),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.memory_service.get_memory", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch(
                "server_modules.sage_agent_runtime_service._resolve_agent_cloud_provider",
                new=AsyncMock(return_value=("anthropic", {"api_key": "sk-agent-key"}, "platform_credits")),
            ),
            patch(
                "server_modules.sage_agent_runtime_service._resolve_cloud_provider",
                return_value=("anthropic", {"api_key": "sk-agent-key"}),
            ),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability",
                return_value={"runtime_ok": True, "local_gateway_online": True},
            ),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat",
            ) as mock_stream,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
            patch.object(sage_agent_runtime_service, "_schedule_post_turn_auto_compaction", new=schedule_mock),
        ):
            mock_stream.return_value = iter(stream_events)
            _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="hello",
                specialist_context=spec,
            ))

        schedule_mock.assert_called_once()
        self.assertEqual(
            schedule_mock.call_args.kwargs["ctx_policy_max"],
            compaction_service.DEFAULT_CONTEXT_WINDOW,
        )


class ModelDowngradeFiresPreflightTests(unittest.TestCase):
    """BUG 6 (the founder's explicit question): if a conversation at ~800k
    tokens switches from a 1M-window model to a 200k-window model, the
    threshold must be recomputed against the NEW model BEFORE the next
    request is built — sending 800k tokens to a 200k-window model
    hard-errors rather than degrading gracefully."""

    @staticmethod
    def _big_prior_messages(total_tokens: int) -> list[dict]:
        # Many turns approximating the given total token count (estimate_
        # tokens is chars // 4) — spread across enough separate messages
        # that find_cut_point_with_fallback has real turns to cut between
        # (a single giant message can't be split at all, which is BUG 2's
        # own "nothing cuttable" case, not what this test is after).
        per_message_tokens = 5_000
        count = max(1, total_tokens // per_message_tokens)
        return [
            {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * (per_message_tokens * 4)}
            for i in range(count)
        ]

    def test_no_compaction_needed_on_the_original_1m_window_model(self):
        prior = self._big_prior_messages(700_000)
        with patch.object(
            sage_agent_runtime_service, "_run_memory_flush_before_compaction",
            new=AsyncMock(return_value=True),
        ), patch(
            "server_modules.compaction_service.compact_turns", new=AsyncMock(),
        ) as compact_mock:
            result = _run(sage_agent_runtime_service._action_loop_context_budget_preflight(
                workspace_id="ws-1", tenant_id="default", thread_id="thread-1",
                # Real 1M-window model.
                provider="anthropic", model="claude-opus-4-8",
                system_prompt="sys", user_message="hi",
                prior_messages=prior, channel_prior_messages=None,
                ctx_policy_max=0, ctx_policy_action="compact",
                used_context=[],
            ))
        self.assertIs(result, prior)
        compact_mock.assert_not_called()

    def test_same_history_fires_compaction_before_dispatch_after_downgrade(self):
        # SAME prior_messages, but the thread's model has switched (mid-
        # conversation) to a 200k-window model — resolve_context_window
        # must reflect THIS call's model, not anything cached from when
        # the conversation started on the 1M model.
        prior = self._big_prior_messages(700_000)
        with patch.object(
            sage_agent_runtime_service, "_run_memory_flush_before_compaction",
            new=AsyncMock(return_value=True),
        ), patch(
            "server_modules.compaction_service.compact_turns",
            new=AsyncMock(return_value="Summary of the downgrade-triggered compaction."),
        ) as compact_mock:
            result = _run(sage_agent_runtime_service._action_loop_context_budget_preflight(
                workspace_id="ws-1", tenant_id="default", thread_id="thread-1",
                # Real 200k-window model — a downgrade from claude-opus-4-8.
                provider="anthropic", model="claude-haiku-4-5-20251001",
                system_prompt="sys", user_message="hi",
                prior_messages=prior, channel_prior_messages=None,
                ctx_policy_max=0, ctx_policy_action="compact",
                used_context=[],
            ))
        # Compaction fired BEFORE any dispatch — this function's entire
        # purpose is to run ahead of _run_sage_action_loop_v3.
        compact_mock.assert_called_once()
        self.assertIsInstance(result, list)
        self.assertTrue(
            any("Summary of the downgrade-triggered compaction." in str(m.get("content") or "") for m in result)
        )


class SubagentSpawnToolVisibilityTests(unittest.TestCase):
    """Structural toggle test (2026-07-24 ruling): subagent__spawn must be
    ABSENT from the actual assembled tool payload handed to the model when
    subagents_enabled is false -- not present-but-discouraged. Exercises the
    real _direct_tool_bundle (no mocking of the function under test); only
    its two workspace-lookup side calls are patched to keep this test
    hermetic (no network/DB), matching this file's established style
    elsewhere (patch.object on direct_chat_runtime_exports)."""

    def _toolset(self, *, subagents_enabled: bool) -> dict:
        return {
            "core": sage_agent_runtime_service._core_direct_tool_names(),
            "connectors": set(),
            "tools": set(),
            "raw_tool_toggles": {},
            "mandate_audience_tools": [],
            "capability_providers": frozenset(),
            "agent_install_id": "agent-pixel",
            "subagents_enabled": subagents_enabled,
        }

    def _tool_names(self, *, subagents_enabled: bool) -> list[str]:
        with patch.object(
            sage_agent_runtime_service.direct_chat_runtime_exports,
            "resolve_workspace_tool_capabilities",
            return_value=[],
        ), patch.object(
            sage_agent_runtime_service.direct_chat_runtime_exports,
            "_resolve_direct_chat_availability",
            return_value={},
        ):
            tools, _caps, _availability, _blocked = sage_agent_runtime_service._direct_tool_bundle(
                workspace_id="ws-test",
                provider="openai",
                sender_class="owner",
                specialist_toolset=self._toolset(subagents_enabled=subagents_enabled),
            )
        return [str(t.get("name") or "") for t in tools]

    def test_tool_absent_from_assembled_payload_when_disabled(self):
        names = self._tool_names(subagents_enabled=False)
        self.assertNotIn("subagent__spawn", names)

    def test_tool_present_in_assembled_payload_when_enabled(self):
        names = self._tool_names(subagents_enabled=True)
        self.assertIn("subagent__spawn", names)

    def test_master_sage_turn_never_gets_the_tool(self):
        # specialist_toolset=None is the master/Sage path -- this feature is
        # scoped to deployed specialist agents only (see build report).
        with patch.object(
            sage_agent_runtime_service.direct_chat_runtime_exports,
            "resolve_workspace_tool_capabilities",
            return_value=[],
        ), patch.object(
            sage_agent_runtime_service.direct_chat_runtime_exports,
            "_resolve_direct_chat_availability",
            return_value={},
        ):
            tools, _caps, _availability, _blocked = sage_agent_runtime_service._direct_tool_bundle(
                workspace_id="ws-test", provider="openai", sender_class="owner", specialist_toolset=None,
            )
        names = [str(t.get("name") or "") for t in tools]
        self.assertNotIn("subagent__spawn", names)

    def test_enabled_schema_requires_task_description_and_offers_role_enum(self):
        with patch.object(
            sage_agent_runtime_service.direct_chat_runtime_exports,
            "resolve_workspace_tool_capabilities",
            return_value=[],
        ), patch.object(
            sage_agent_runtime_service.direct_chat_runtime_exports,
            "_resolve_direct_chat_availability",
            return_value={},
        ):
            tools, _caps, _availability, _blocked = sage_agent_runtime_service._direct_tool_bundle(
                workspace_id="ws-test",
                provider="openai",
                sender_class="owner",
                specialist_toolset=self._toolset(subagents_enabled=True),
            )
        spawn_tool = next(t for t in tools if t.get("name") == "subagent__spawn")
        params = spawn_tool.get("parameters") or {}
        self.assertIn("task_description", params.get("required") or [])
        role_enum = (params.get("properties") or {}).get("role", {}).get("enum") or []
        self.assertIn("builder", role_enum)
        self.assertNotIn("orchestrator", role_enum)


class ProjectTaskToolTier1VisibilityTests(unittest.TestCase):
    """fix/agent-task-tools-on-sdk-engine: project_task__* must be a
    STRUCTURAL Tier-1 carve-out for a project-member specialist -- present
    in the actual assembled tool payload handed to the model, not just
    discoverable via Tier-2 query_tool_registry (a door the Claude Agent SDK
    engine does not have at all -- claude_agent_sdk_bridge.
    _UNSUPPORTED_TOOL_NAMES). Mirrors SubagentSpawnToolVisibilityTests'
    style: exercises the real _direct_tool_bundle, only its two
    workspace-lookup side calls patched."""

    _PROJECT_TASK_NAMES = {
        "project_task__create", "project_task__list", "project_task__get",
        "project_task__set_parent", "project_task__update", "project_task__comment",
        "project_task__assign", "project_task__list_labels", "project_task__add_label",
        "project_task__remove_label",
    }

    def _toolset(self, *, project_id: str) -> dict:
        return {
            "core": sage_agent_runtime_service._core_direct_tool_names(),
            "connectors": set(),  # "project_task" never bound -- no path to bind it
            "tools": set(),
            "raw_tool_toggles": {},
            "mandate_audience_tools": [],
            "capability_providers": frozenset(),
            "agent_install_id": "agent-pixel",
            "subagents_enabled": False,
            "project_id": project_id,
        }

    def _tool_names(self, *, specialist_toolset) -> list[str]:
        with patch.object(
            sage_agent_runtime_service.direct_chat_runtime_exports,
            "resolve_workspace_tool_capabilities",
            return_value=[],
        ), patch.object(
            sage_agent_runtime_service.direct_chat_runtime_exports,
            "_resolve_direct_chat_availability",
            return_value={},
        ):
            tools, _caps, _availability, _blocked = sage_agent_runtime_service._direct_tool_bundle(
                workspace_id="ws-test",
                provider="openai",
                sender_class="owner",
                specialist_toolset=specialist_toolset,
            )
        return [str(t.get("name") or "") for t in tools]

    def test_project_member_specialist_gets_all_project_task_tools_in_tier1(self):
        names = set(self._tool_names(specialist_toolset=self._toolset(project_id="proj-1")))
        self.assertTrue(
            self._PROJECT_TASK_NAMES.issubset(names),
            f"missing: {self._PROJECT_TASK_NAMES - names}",
        )

    def test_non_member_specialist_never_gets_project_task_tools(self):
        names = set(self._tool_names(specialist_toolset=self._toolset(project_id="")))
        self.assertFalse(names & self._PROJECT_TASK_NAMES, f"unexpected leak: {names & self._PROJECT_TASK_NAMES}")

    def test_master_sage_turn_never_gets_project_task_tools(self):
        # specialist_toolset=None is the master/Sage path -- the master
        # identity is workspace-scoped with no single owning project
        # (skills_service.py raises at execution time for it), so this is
        # deliberately NOT the fleet__* shape (master-only carve-out) --
        # it's the opposite scoping.
        names = set(self._tool_names(specialist_toolset=None))
        self.assertFalse(names & self._PROJECT_TASK_NAMES, f"unexpected leak: {names & self._PROJECT_TASK_NAMES}")

    def test_project_task_tool_schema_is_well_formed(self):
        names_and_tools = {}
        with patch.object(
            sage_agent_runtime_service.direct_chat_runtime_exports,
            "resolve_workspace_tool_capabilities", return_value=[],
        ), patch.object(
            sage_agent_runtime_service.direct_chat_runtime_exports,
            "_resolve_direct_chat_availability", return_value={},
        ):
            tools, _caps, _availability, _blocked = sage_agent_runtime_service._direct_tool_bundle(
                workspace_id="ws-test", provider="openai", sender_class="owner",
                specialist_toolset=self._toolset(project_id="proj-1"),
            )
        for t in tools:
            names_and_tools[t.get("name")] = t
        create_tool = names_and_tools["project_task__create"]
        self.assertIn("title", (create_tool.get("parameters") or {}).get("properties", {}))
        self.assertEqual(create_tool.get("connector_id"), "project_task")


class CollectSageOperatorLoopV3EventsToolResultStatusTests(unittest.TestCase):
    """_collect_sage_operator_loop_v3_events builds the tool_calls list that
    handle_sage_chat hands to tool_honesty_guard.apply_tool_honesty_guard as
    tool_trace (see _run_sage_action_loop_v3's return and its call site). A
    soft-failure status wrongly landing here as "completed" would mean the
    honesty guard's fabrication-after-failure check (tool_honesty_guard.py's
    claims_success_after_failure direction) could never fire on this
    pipeline — the exact "matters enormously" classification question the
    2026-08-01 hardware fabrication incident turned on. This pins the fix:
    classification is delegated to tool_result_status.classify_tool_result
    (the same structural verdict every other producer in the codebase uses)
    instead of a hand-rolled {"error", "failed"} status-token set."""

    @staticmethod
    def _trace_event(*, tool_call_id: str, status: str, summary: str = "") -> dict:
        return {
            "type": "trace",
            "payload": {
                "event_type": "tool.result",
                "tool_call_id": tool_call_id,
                "data": {"status": status, "summary": summary},
            },
        }

    def test_offline_status_classifies_as_failed_not_completed(self) -> None:
        events = [
            self._trace_event(
                tool_call_id="call-1",
                status="offline",
                summary="Gateway is not ready for this hardware action.",
            ),
        ]
        collected = sage_agent_runtime_service._collect_sage_operator_loop_v3_events(events)
        entry = collected["tool_calls"][0]
        self.assertEqual(entry["status"], "failed")
        self.assertEqual(entry["error"], "Gateway is not ready for this hardware action.")
        self.assertNotIn("output", entry)

    def test_degraded_status_classifies_as_failed(self) -> None:
        events = [self._trace_event(tool_call_id="call-1", status="degraded", summary="Needs a runtime target.")]
        collected = sage_agent_runtime_service._collect_sage_operator_loop_v3_events(events)
        self.assertEqual(collected["tool_calls"][0]["status"], "failed")

    def test_ok_status_still_classifies_as_completed(self) -> None:
        # No regression: the real producer (direct_chat_generation_service.py's
        # own tool.result yields) only ever emits "ok"/"failed"/"error" — this
        # pins that unchanged behavior alongside the newly-fixed tokens.
        events = [self._trace_event(tool_call_id="call-1", status="ok", summary="Found it.")]
        collected = sage_agent_runtime_service._collect_sage_operator_loop_v3_events(events)
        entry = collected["tool_calls"][0]
        self.assertEqual(entry["status"], "completed")
        self.assertEqual(entry["output"], "Found it.")

    def test_error_and_failed_statuses_still_classify_as_failed(self) -> None:
        for status in ("error", "failed"):
            with self.subTest(status=status):
                events = [self._trace_event(tool_call_id="call-1", status=status, summary="boom")]
                collected = sage_agent_runtime_service._collect_sage_operator_loop_v3_events(events)
                self.assertEqual(collected["tool_calls"][0]["status"], "failed")

    def test_waiting_approval_status_does_not_classify_as_failed(self) -> None:
        # A hardware action parked on approval has not failed — must stay
        # off the failed list or a routine approval would look like a crash.
        events = [self._trace_event(tool_call_id="call-1", status="waiting_approval", summary="Waiting for approval.")]
        collected = sage_agent_runtime_service._collect_sage_operator_loop_v3_events(events)
        self.assertEqual(collected["tool_calls"][0]["status"], "completed")


class ClassifySageNoReplyOutcomeTests(unittest.TestCase):
    """_classify_sage_no_reply_outcome decides which of TOOLS_LIMITED_
    NO_REPLY / SAGE_TURN_NO_REPLY_UNKNOWN a no-natural-language-reply turn
    gets. 2026-08-14 fix: the previous logic treated ANY non-empty
    blocked_tools list as proof a tool was blocked by policy, but every real
    blocked_tools producer today (claude_agent_sdk_bridge.py's provider-
    error/foreign-tool/orphan-tool-result trace.failed events,
    direct_chat_generation_service.py's cost-ceiling/tool-loop-detected/
    generation-error trace.failed events) is a FAILURE, not a policy
    decision — see server_modules/sage_blocked_tools_outcome.py's own
    comment (the classifier and its SAGE_BLOCKED_TOOLS_POLICY_CODES
    allowlist moved there 2026-08-14 so sage_transparency_service.py's
    Work-tab/Inbox event emission could reuse them instead of growing its
    own copy; _classify_sage_no_reply_outcome / _SAGE_BLOCKED_TOOLS_
    POLICY_CODES here are just the original private names, re-exported).
    These tests pin the classifier directly, independent of the full turn
    plumbing exercised in SageAgentRuntimeResultShapeTests."""

    def test_empty_blocked_tools_classifies_as_none(self) -> None:
        self.assertEqual(sage_agent_runtime_service._classify_sage_no_reply_outcome([]), "none")

    def test_known_provider_failure_code_classifies_as_turn_failure(self) -> None:
        for code in ("provider_generation_failed", "foreign_tool_call", "orphan_tool_result", "operator_loop_failed"):
            with self.subTest(code=code):
                blocked = [{"name": code, "reason": code, "status": "blocked"}]
                self.assertEqual(
                    sage_agent_runtime_service._classify_sage_no_reply_outcome(blocked),
                    "turn_failure",
                )

    def test_raw_sdk_subtype_code_classifies_as_turn_failure(self) -> None:
        # claude_agent_sdk_bridge.py passes a genuine ResultMessage subtype
        # (e.g. "error_max_turns") through verbatim as the blocked entry's
        # code — this module cannot enumerate every subtype the SDK might
        # ever emit, so anything not in the explicit policy allowlist stays
        # "turn_failure" (the honest-by-default direction), not just a
        # hand-picked set of known codes.
        blocked = [{"name": "error_max_turns", "reason": "error_max_turns", "status": "blocked"}]
        self.assertEqual(
            sage_agent_runtime_service._classify_sage_no_reply_outcome(blocked),
            "turn_failure",
        )

    def test_unrecognized_code_classifies_as_turn_failure_not_policy_blocked(self) -> None:
        # The core of the fix: a code this function has never seen must NOT
        # default to "tools are the cause" — that is exactly the blind spot
        # that produced the founder-reported bug.
        blocked = [{"name": "something_nobody_has_seen_before", "reason": "?", "status": "blocked"}]
        self.assertEqual(
            sage_agent_runtime_service._classify_sage_no_reply_outcome(blocked),
            "turn_failure",
        )

    def test_recognized_policy_code_classifies_as_policy_blocked(self) -> None:
        # No live producer emits a recognized policy code today (the
        # allowlist is empty) — this proves the MECHANISM still works for a
        # future one, via patching rather than inventing a fake producer
        # shape (see the sibling turn-level test for why that distinction
        # matters).
        with patch(
            "server_modules.sage_blocked_tools_outcome.SAGE_BLOCKED_TOOLS_POLICY_CODES",
            frozenset({"agent_tool_capability_denied"}),
        ):
            blocked = [{"name": "agent_tool_capability_denied", "reason": "not bound", "status": "blocked"}]
            self.assertEqual(
                sage_agent_runtime_service._classify_sage_no_reply_outcome(blocked),
                "policy_blocked",
            )

    def test_mixed_policy_and_unrecognized_codes_classifies_as_turn_failure(self) -> None:
        # One entry the function cannot positively identify as a policy
        # decision is enough to withhold the tools-settings claim for the
        # WHOLE turn, even alongside an entry that IS recognized.
        with patch(
            "server_modules.sage_blocked_tools_outcome.SAGE_BLOCKED_TOOLS_POLICY_CODES",
            frozenset({"agent_tool_capability_denied"}),
        ):
            blocked = [
                {"name": "agent_tool_capability_denied", "reason": "not bound", "status": "blocked"},
                {"name": "provider_generation_failed", "reason": "server error", "status": "blocked"},
            ]
            self.assertEqual(
                sage_agent_runtime_service._classify_sage_no_reply_outcome(blocked),
                "turn_failure",
            )


class CollectSageOperatorLoopV3EventsMetaToolCallsTests(unittest.TestCase):
    """MAN-310 skills-delivery: skill.invoked/subagent.invoked trace events
    (claude_agent_sdk_bridge's honest meta-tool event types) land in their
    OWN `meta_tool_calls` bucket — never tool_calls (tool_honesty_guard's
    own input) and never blocked_tools (a real failure)."""

    @staticmethod
    def _meta_event(*, event_type: str, tool_call_id: str, phase: str, **data) -> dict:
        return {
            "type": "trace",
            "payload": {"event_type": event_type, "tool_call_id": tool_call_id, "data": {"phase": phase, **data}},
        }

    def test_skill_invoked_never_enters_tool_calls_or_blocked_tools(self) -> None:
        events = [
            self._meta_event(event_type="skill.invoked", tool_call_id="c1", phase="started", tool_name="Skill"),
            self._meta_event(event_type="skill.invoked", tool_call_id="c1", phase="result", status="ok", summary="done"),
        ]
        collected = sage_agent_runtime_service._collect_sage_operator_loop_v3_events(events)
        self.assertEqual(collected["tool_calls"], [])
        self.assertEqual(collected["blocked_tools"], [])
        self.assertEqual(len(collected["meta_tool_calls"]), 1)
        entry = collected["meta_tool_calls"][0]
        self.assertEqual(entry["kind"], "skill")
        self.assertEqual(entry["status"], "completed")
        self.assertEqual(entry["summary"], "done")

    def test_subagent_invoked_is_bucketed_with_kind_subagent(self) -> None:
        events = [self._meta_event(event_type="subagent.invoked", tool_call_id="c2", phase="started", tool_name="Agent")]
        collected = sage_agent_runtime_service._collect_sage_operator_loop_v3_events(events)
        self.assertEqual(collected["meta_tool_calls"][0]["kind"], "subagent")

    def test_failed_meta_tool_result_is_still_not_a_blocked_tool(self) -> None:
        events = [
            self._meta_event(event_type="skill.invoked", tool_call_id="c3", phase="started", tool_name="Skill"),
            self._meta_event(event_type="skill.invoked", tool_call_id="c3", phase="result", status="failed", summary="broke"),
        ]
        collected = sage_agent_runtime_service._collect_sage_operator_loop_v3_events(events)
        self.assertEqual(collected["blocked_tools"], [])
        self.assertEqual(collected["meta_tool_calls"][0]["status"], "failed")

    def test_a_meta_tool_only_turn_is_still_text_only(self) -> None:
        # Empyralis's own perspective: a turn that only invoked a skill and
        # called no Empyralis tool really did no Empyralis-tool work.
        events = [
            self._meta_event(event_type="skill.invoked", tool_call_id="c4", phase="started", tool_name="Skill"),
            {"type": "final", "payload": {"reply": "Here is your answer."}},
        ]
        collected = sage_agent_runtime_service._collect_sage_operator_loop_v3_events(events)
        self.assertEqual(collected["action_execution_mode"], "text_only")

    def test_no_meta_tool_events_yields_an_empty_bucket(self) -> None:
        collected = sage_agent_runtime_service._collect_sage_operator_loop_v3_events([])
        self.assertEqual(collected["meta_tool_calls"], [])


class ResolveSpecialistToolsetSkillsTests(unittest.TestCase):
    """_resolve_specialist_toolset's own `skills` key — the glue between
    fleet_tools.resolve_agent_skills and claude_agent_sdk_bridge.run_
    claude_agent_sdk_turn's `skills=` parameter (see _run_sage_action_loop_
    v3's _collect_stream_events closure, which forwards this straight
    through)."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_enabled_skills_are_resolved_onto_the_toolset(self) -> None:
        bundle = {
            "install_metadata": {
                "skills": [
                    {"name": "On", "description": "d", "body": "b", "kind": "skill", "enabled": True},
                    {"name": "Off", "description": "d", "body": "b", "kind": "skill", "enabled": False},
                ],
            },
            "tool_toggles": {},
        }
        with (
            patch(
                "server_modules.agent_bindings_repository.list_agent_connector_bindings",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=bundle),
            ),
        ):
            toolset = self._run(sage_agent_runtime_service._resolve_specialist_toolset(
                workspace_id="ws-1", tenant_id="default", agent_install_id="agent-1",
            ))
        self.assertEqual([s["name"] for s in toolset["skills"]], ["On"])

    def test_no_skills_configured_resolves_to_an_empty_list(self) -> None:
        bundle = {"install_metadata": {}, "tool_toggles": {}}
        with (
            patch(
                "server_modules.agent_bindings_repository.list_agent_connector_bindings",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=bundle),
            ),
        ):
            toolset = self._run(sage_agent_runtime_service._resolve_specialist_toolset(
                workspace_id="ws-1", tenant_id="default", agent_install_id="agent-1",
            ))
        self.assertEqual(toolset["skills"], [])

    def test_bundle_load_failure_fails_safe_to_no_skills(self) -> None:
        with (
            patch(
                "server_modules.agent_bindings_repository.list_agent_connector_bindings",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(side_effect=RuntimeError("control plane unavailable")),
            ),
        ):
            toolset = self._run(sage_agent_runtime_service._resolve_specialist_toolset(
                workspace_id="ws-1", tenant_id="default", agent_install_id="agent-1",
            ))
        self.assertEqual(toolset["skills"], [])

    def test_master_agent_path_never_resolves_a_toolset_at_all(self) -> None:
        # agent_install_id="" is the master/Sage path — _resolve_specialist_
        # toolset returns None outright, same architectural boundary
        # persona/instructions already draws (specialist_runtime_context.py).
        toolset = self._run(sage_agent_runtime_service._resolve_specialist_toolset(
            workspace_id="ws-1", tenant_id="default", agent_install_id="",
        ))
        self.assertIsNone(toolset)


if __name__ == "__main__":
    unittest.main()

import asyncio
import os
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from server_modules.agent_turn import AgentTurnRequest, TurnActor
from server_modules import direct_tool_execution_service
from server_modules import no_provider_service
from server_modules import skills_service


class SkillsServiceTests(unittest.TestCase):
    def _execution_callbacks(self) -> direct_tool_execution_service.DirectToolExecutionCallbacks:
        return direct_tool_execution_service.DirectToolExecutionCallbacks(
            compact_step_detail=lambda value: " ".join(str(value or "").split()).strip() or None,
            titleize_direct_step_token=lambda value: " ".join(word.capitalize() for word in str(value or "").split("_")),
            run_async_tool_call=lambda awaitable: awaitable,
            parse_tool_name=lambda name: (
                tuple(str(name or "").split("__", 1)) if "__" in str(name or "") else tuple(str(name or "").split("_", 1))
            ),
            tool_arguments_payload=lambda payload: payload if isinstance(payload, dict) else {},
            parse_json_object_loose=lambda value: {},
            safe_positive_int=lambda value, default=0: int(value) if str(value or "").strip().isdigit() else default,
            normalize_reasoning_effort=lambda value: str(value or "").strip().lower() or None,
            build_direct_local_tool_config=skills_service.build_direct_local_tool_config,
            format_direct_local_tool_result=lambda result: json.dumps(result, ensure_ascii=False),
            build_direct_tool_config=lambda connector_id, action_id, tool_input: {
                "connector": connector_id,
                "action": action_id,
                "input": tool_input,
            },
            format_direct_tool_result=lambda result: json.dumps(result, ensure_ascii=False),
            llm_task=lambda *args, **kwargs: {"ok": True},
            web_search=lambda query: [],
            web_fetch=lambda url: f"Fetched {url}",
            search_memory_notebook=lambda workspace_id, query, max_results=5: [{"path": "MEMORY.md", "query": query, "max_results": max_results}],
            get_memory_notebook_excerpt=lambda workspace_id, rel_path, from_line=None, line_count=None: {
                "path": rel_path,
                "from_line": from_line,
                "line_count": line_count,
            },
            update_memory_context_file=lambda workspace_id, filename, content, agent_install_id=None, **kwargs: {
                "workspace_id": workspace_id,
                "filename": filename,
                "content": content,
                "agent_install_id": agent_install_id,
                "version_id": kwargs.get("version_id") or "version-1",
            },
            memory_append_daily_note=lambda workspace_id, note, agent_install_id=None, actor=None, run_id=None: {
                "workspace_id": workspace_id,
                "filename": "memory/2026-05-11.md",
                "appended_entry": f"- [00:00:00 UTC] {note}",
                "saved": True,
                "usefulness": "useful",
                "agent_install_id": agent_install_id,
            },
            create_memory_consolidation_staging_file=lambda workspace_id, proposal, source_refs=None, target_files=None, agent_install_id=None, actor=None, run_id=None: {
                "workspace_id": workspace_id,
                "filename": "memory/.dreams/20260511T000000Z-abc1234567.md",
                "source_refs": list(source_refs or []),
                "target_files": list(target_files or []),
                "agent_install_id": agent_install_id,
            },
            consolidate_daily_memory_notes=lambda workspace_id, **kwargs: {
                "workspace_id": workspace_id,
                "proposal_id": "proposal-1",
                "proposed_updates": {"MEMORY.md": "# Curated Memory\n\n- consolidated\n"},
                "merged": bool(kwargs.get("apply_merge")),
                "audit_id": "audit-1" if kwargs.get("apply_merge") else None,
                "compact_mode": kwargs.get("compact_mode") or "none",
            },
            apply_memory_consolidation_staging=lambda workspace_id, staging_filename, merged_files, **kwargs: {
                "workspace_id": workspace_id,
                "staging_filename": staging_filename,
                "merged_files": dict(merged_files),
                "audit_id": "audit-apply-1",
                "user_approved": bool(kwargs.get("user_approved")),
            },
            list_memory_file_versions=lambda workspace_id, filename, agent_install_id=None, limit=20: [
                {
                    "version_id": "ver-1",
                    "workspace_id": workspace_id,
                    "agent_install_id": agent_install_id,
                    "filename": filename,
                    "old_hash": "old",
                    "new_hash": "new",
                    "reason": "memory_update",
                    "actor": "direct_tool",
                    "timestamp": "2026-05-11T00:00:00Z",
                }
            ],
            rollback_memory_file_version=lambda workspace_id, filename, **kwargs: {
                "workspace_id": workspace_id,
                "filename": filename,
                "rolled_back_to_version_id": kwargs.get("version_id"),
                "new_version_id": "ver-2",
                "new_hash": "hash-2",
            },
        )

    def test_safe_direct_shell_allows_known_read_only_system_probe_only(self) -> None:
        self.assertTrue(
            skills_service._safe_direct_shell_command(no_provider_service.local_system_info_shell_command())
        )
        self.assertFalse(skills_service._safe_direct_shell_command("pwd; echo unsafe"))

    def test_capability_descriptor_from_payload_normalizes_fields(self) -> None:
        descriptor = skills_service.capability_descriptor_from_payload(
            {
                "id": " Slack ",
                "label": " Slack Live ",
                "connected": True,
                "authenticated": True,
                "runtime_usable": False,
                "read_actions": [" history.read ", ""],
                "write_actions": [" post_message "],
                "approval_required_actions": ["post_message", "post_message"],
            }
        )

        assert descriptor is not None
        self.assertEqual(descriptor.capability_id, "slack")
        self.assertEqual(descriptor.label, "Slack Live")
        self.assertTrue(descriptor.requires_approval)
        self.assertEqual(descriptor.risk_level, "medium")
        self.assertEqual(descriptor.metadata["read_actions"], ["history.read"])
        self.assertEqual(descriptor.metadata["write_actions"], ["post_message"])
        self.assertEqual(descriptor.metadata["approval_required_actions"], ["post_message"])

    def test_normalize_capability_payloads_filters_invalid_items(self) -> None:
        payload = skills_service.normalize_capability_payloads(
            [
                {"id": "browser", "connected": True},
                {"id": ""},
                "skip",
            ]
        )

        self.assertEqual(
            payload,
            [
                {
                    "id": "browser",
                    "label": "browser",
                    "risk_level": "medium",
                    "requires_approval": False,
                    "connected": True,
                    "authenticated": None,
                    "runtime_usable": None,
                    "read_actions": [],
                    "write_actions": [],
                    "approval_required_actions": [],
                }
            ],
        )

    def test_resolve_workspace_capability_payloads_normalizes_resolver_result(self) -> None:
        payload = skills_service.resolve_workspace_capability_payloads(
            "workspace-a",
            resolve_workspace_tool_capabilities_fn=lambda workspace_id: [
                {"id": " Gmail ", "workspace_id": workspace_id, "connected": True}
            ],
        )

        self.assertEqual(payload[0]["id"], "gmail")
        self.assertEqual(payload[0]["connected"], True)

    def test_availability_capability_helpers_read_normalized_payload(self) -> None:
        availability = {
            "tool_capabilities": [
                {"id": " Browser ", "connected": True, "runtime_usable": False},
            ]
        }

        self.assertEqual(skills_service.availability_capability(availability, "browser")["id"], "browser")
        self.assertTrue(skills_service.availability_capability_connected(availability, "browser"))
        self.assertFalse(skills_service.availability_capability_runtime_usable(availability, "browser"))
        self.assertIsNone(skills_service.availability_capability(availability, "missing"))

    def test_capability_action_helpers_normalize_write_and_approval_actions(self) -> None:
        availability = {
            "tool_capabilities": [
                {
                    "id": " Slack ",
                    "connected": True,
                    "write_actions": [" post_message ", "send_dm", "post_message"],
                    "approval_required_actions": [" send_dm "],
                },
            ]
        }

        self.assertEqual(
            skills_service.availability_capability_write_actions(availability, "slack"),
            ["post_message", "send_dm"],
        )
        self.assertEqual(
            skills_service.availability_capability_approval_required_actions(availability, "slack"),
            ["send_dm"],
        )
        self.assertTrue(skills_service.availability_capability_supports_write_action(availability, "slack", "post_message"))
        self.assertTrue(
            skills_service.availability_capability_requires_approval_for_action(
                availability,
                "slack",
                "send_dm",
            )
        )
        self.assertFalse(
            skills_service.availability_capability_supports_write_action(
                {"tool_capabilities": [{"id": "slack", "connected": False, "write_actions": ["post_message"]}]},
                "slack",
                "post_message",
            )
        )

    def test_connected_and_context_availability_helpers(self) -> None:
        availability = {
            "tool_capabilities": [
                {
                    "id": "slack",
                    "label": "Slack",
                    "connected": True,
                    "runtime_usable": True,
                    "read_actions": ["history.read", "channels.read"],
                    "write_actions": ["post_message", "send_dm"],
                    "approval_required_actions": ["post_message"],
                },
                {
                    "id": "telegram",
                    "label": "Telegram",
                    "connected": True,
                    "runtime_usable": None,
                },
                {
                    "id": "dropbox",
                    "label": "Dropbox",
                    "connected": True,
                    "runtime_usable": False,
                },
                {"id": "github", "label": "GitHub", "connected": False},
            ]
        }

        self.assertEqual(skills_service.connected_availability_labels(availability), ["Slack", "Telegram", "Dropbox"])
        self.assertEqual(skills_service.unavailable_connected_availability_labels(availability), ["Dropbox"])
        self.assertEqual(skills_service.unverified_connected_availability_labels(availability), ["Telegram"])
        context_payload = skills_service.context_availability_capabilities(
            availability,
            max_context_tool_actions=1,
            max_context_tool_capabilities=2,
        )
        self.assertEqual(len(context_payload), 2)
        self.assertEqual(context_payload[0]["read_actions"], ["history.read"])
        self.assertEqual(context_payload[0]["write_actions"], ["post_message"])

    def test_availability_label_summary_groups_connected_states(self) -> None:
        availability = {
            "tool_capabilities": [
                {"id": "slack", "label": "Slack", "connected": True, "runtime_usable": True},
                {"id": "telegram", "label": "Telegram", "connected": True, "runtime_usable": False},
                {"id": "gmail", "label": "Google Workspace", "connected": True, "runtime_usable": None},
                {"id": "github", "label": "GitHub", "connected": False},
            ]
        }

        summary = skills_service.availability_label_summary(availability)

        self.assertEqual(summary["connected"], ["Slack", "Telegram", "Google Workspace"])
        self.assertEqual(summary["usable"], ["Slack"])
        self.assertEqual(summary["unavailable"], ["Telegram"])
        self.assertEqual(summary["unverified"], ["Google Workspace"])

    def test_tool_registry_builds_connector_and_local_tools(self) -> None:
        connector_tools = skills_service.build_direct_chat_tools(
            [
                {
                    "id": "slack",
                    "label": "Slack",
                    "connected": True,
                    "runtime_usable": True,
                    "write_actions": ["post_message"],
                },
                {
                    "id": "dropbox",
                    "label": "Dropbox",
                    "connected": True,
                    "runtime_usable": False,
                    "write_actions": ["upload_file"],
                },
            ]
        )
        local_tools = skills_service.build_local_direct_chat_tools(
            {"runtime_ok": True},
            local_worker_available=lambda availability: True,
        )

        self.assertEqual([item["name"] for item in connector_tools], ["slack__post_message"])
        slack_post = connector_tools[0]
        self.assertEqual(slack_post["connector_id"], "slack")
        self.assertEqual(slack_post["action_id"], "post_message")
        self.assertEqual(slack_post["capability_id"], "connector.action.write")
        self.assertEqual(slack_post["action_class"], "write")
        self.assertTrue(slack_post["requires_approval"])
        self.assertEqual(slack_post["permission_manifest"]["scopes"], ["connector.action.write", "slack:post_message"])
        self.assertEqual(slack_post["permission_manifest"]["allowed_runtime_modes"], ["hosted_secure", "local_secure"])
        self.assertEqual(slack_post["permission_manifest"]["audit_event_type"], "direct_tool.slack.post_message")
        self.assertTrue(any(item["name"] == "file__read" for item in local_tools))
        self.assertTrue(any(item["name"] == "computer__click" for item in local_tools))
        file_read = next(item for item in local_tools if item["name"] == "file__read")
        computer_click = next(item for item in local_tools if item["name"] == "computer__click")
        self.assertEqual(file_read["capability_id"], "filesystem.read")
        self.assertEqual(file_read["risk_level"], "medium")
        self.assertTrue(file_read["requires_approval"])
        self.assertEqual(file_read["action_class"], "read")
        self.assertEqual(file_read["permission_manifest"]["scopes"], ["filesystem.read"])
        self.assertEqual(file_read["permission_manifest"]["allowed_runtime_modes"], ["local_secure"])
        self.assertEqual(file_read["permission_manifest"]["audit_event_type"], "direct_tool.file.read")
        self.assertEqual(computer_click["capability_id"], "computer_control.click")
        self.assertEqual(computer_click["risk_level"], "critical")
        self.assertTrue(computer_click["requires_approval"])
        self.assertEqual(computer_click["permission_manifest"]["allowed_runtime_modes"], ["privileged_device"])

    def test_builtin_tool_registry_keeps_browser_schema_and_permission_manifest(self) -> None:
        tools = skills_service.build_builtin_direct_chat_tools()

        browser_navigate = next(item for item in tools if item["name"] == "browser__navigate")
        http_request = next(item for item in tools if item["name"] == "http_request")

        self.assertEqual(browser_navigate["capability_id"], "browser_automation.interactive")
        self.assertIn("url", browser_navigate["parameters"]["properties"])
        self.assertEqual(browser_navigate["permission_manifest"]["scopes"], ["browser_automation.interactive"])
        self.assertEqual(browser_navigate["permission_manifest"]["allowed_runtime_modes"], ["local_secure", "hosted_secure"])
        self.assertEqual(browser_navigate["permission_manifest"]["audit_event_type"], "direct_tool.browser.navigate")
        self.assertEqual(http_request["capability_id"], "http_request")
        self.assertTrue(http_request["requires_approval"])
        self.assertEqual(http_request["permission_manifest"]["cost_class"], "external")

    def test_tool_registry_resolves_local_and_http_action_availability(self) -> None:
        self.assertTrue(skills_service.tool_write_action_available("file", "read", []))
        self.assertTrue(skills_service.tool_write_action_available("http", "request", []))
        self.assertFalse(skills_service.tool_write_action_available("browser", "click", []))

    def test_capability_action_metadata_tracks_approval_requirements(self) -> None:
        metadata = skills_service.capability_action_metadata(
            [
                {
                    "id": "slack",
                    "connected": True,
                    "runtime_usable": True,
                    "write_actions": ["post_message"],
                    "approval_required_actions": ["post_message"],
                }
            ],
            "slack",
            "post_message",
        )

        self.assertTrue(metadata["connected"])
        self.assertTrue(metadata["runtime_usable"])
        self.assertTrue(metadata["supports_write_action"])
        self.assertTrue(metadata["requires_approval"])
        self.assertTrue(
            skills_service.tool_action_requires_approval(
                "slack",
                "post_message",
                [
                    {
                        "id": "slack",
                        "connected": True,
                        "runtime_usable": True,
                        "write_actions": ["post_message"],
                        "approval_required_actions": ["post_message"],
                    }
                ],
            )
        )

    def test_approved_action_to_tool_call_uses_registry_lookup(self) -> None:
        http_payload = skills_service.approved_action_to_tool_call(
            {"connector": "http", "action": "request", "input": "{\"url\":\"https://example.com\"}"},
            parse_json_object_loose=lambda value: json.loads(value),
        )
        connector_payload = skills_service.approved_action_to_tool_call(
            {"connector": "slack", "action": "post_message", "input": "hello"},
            parse_json_object_loose=lambda value: {},
        )

        self.assertEqual(http_payload["name"], "http_request")
        self.assertIn("https://example.com", http_payload["arguments"])
        self.assertEqual(connector_payload["name"], "slack__post_message")
        self.assertEqual(json.loads(connector_payload["arguments"]), {"input": "hello"})

    def test_build_direct_tool_config_builds_google_workspace_email_payload(self) -> None:
        payload = skills_service.build_direct_tool_config(
            "google_workspace",
            "send_email",
            "to john@example.com subject Demo body Hello there",
            parse_json_object_loose=lambda value: {},
        )

        self.assertEqual(payload["to_email"], "john@example.com")
        self.assertEqual(payload["subject"], "Demo")
        self.assertEqual(payload["text"], "Hello there")

    def test_build_direct_local_tool_config_requires_computer_click_target(self) -> None:
        with self.assertRaises(RuntimeError):
            skills_service.build_direct_local_tool_config("computer", "click", {})

    def test_execute_single_direct_tool_call_dispatches_memory_tools(self) -> None:
        callbacks = self._execution_callbacks()

        search_raw = skills_service.execute_single_direct_tool_call(
            tool_call={"name": "memory_search", "arguments": {"query": "timezone", "max_results": 3}},
            workspace_id="default",
            thread_id="thread-1",
            callbacks=callbacks,
        )
        get_raw = skills_service.execute_single_direct_tool_call(
            tool_call={"name": "memory_get", "arguments": {"path": "MEMORY.md", "from": 2, "lines": 4}},
            workspace_id="default",
            thread_id="thread-1",
            callbacks=callbacks,
        )

        self.assertEqual(json.loads(search_raw)["results"][0]["query"], "timezone")
        self.assertEqual(json.loads(search_raw)["results"][0]["max_results"], 3)
        self.assertEqual(json.loads(get_raw)["path"], "MEMORY.md")
        self.assertEqual(json.loads(get_raw)["from_line"], 2)

    def test_execute_single_direct_tool_call_dispatches_memory_update(self) -> None:
        raw = skills_service.execute_single_direct_tool_call(
            tool_call={"name": "memory_update", "arguments": {"filename": "GOALS.md", "content": "# Goals\n\n- Ship memory editing\n"}},
            workspace_id="default",
            thread_id="thread-1",
            callbacks=self._execution_callbacks(),
        )

        payload = json.loads(raw)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["filename"], "GOALS.md")

    def test_execute_single_direct_tool_call_dispatches_memory_stage_edit_and_apply_edit(self) -> None:
        stage_raw = skills_service.execute_single_direct_tool_call(
            tool_call={
                "name": "memory_stage_edit",
                "arguments": {
                    "filename": "IDENTITY.md",
                    "content": "# Identity\n\n- Be direct and evidence-led.\n",
                    "reason": "user requested identity update",
                    "source_refs": ["chat://thread-1#turn-3"],
                },
            },
            workspace_id="default",
            thread_id="thread-1",
            callbacks=self._execution_callbacks(),
        )

        stage_payload = json.loads(stage_raw)
        self.assertTrue(stage_payload["ok"])
        self.assertTrue(stage_payload["staged_only"])
        self.assertTrue(stage_payload["approval_required"])
        self.assertEqual(stage_payload["target_files"], ["IDENTITY.md"])

        apply_raw = skills_service.execute_single_direct_tool_call(
            tool_call={
                "name": "memory_apply_edit",
                "arguments": {
                    "staging_filename": stage_payload["filename"],
                    "merged_files": {"IDENTITY.md": "# Identity\n\n- Be direct and evidence-led.\n"},
                    "user_approved": True,
                },
            },
            workspace_id="default",
            thread_id="thread-1",
            callbacks=self._execution_callbacks(),
        )

        apply_payload = json.loads(apply_raw)
        self.assertEqual(apply_payload["audit_id"], "audit-apply-1")
        self.assertTrue(apply_payload["user_approved"])
        self.assertEqual(apply_payload["merged_files"]["IDENTITY.md"], "# Identity\n\n- Be direct and evidence-led.\n")

    def test_execute_single_direct_tool_call_dispatches_memory_append_daily_note(self) -> None:
        raw = skills_service.execute_single_direct_tool_call(
            tool_call={
                "name": "memory_append_daily_note",
                "arguments": {"note": "Decision: enforce explicit runtime placement for high-cost actions."},
            },
            workspace_id="default",
            thread_id="thread-1",
            callbacks=self._execution_callbacks(),
        )

        payload = json.loads(raw)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["filename"], "memory/2026-05-11.md")
        self.assertTrue(payload["saved"])
        self.assertEqual(payload["usefulness"], "useful")
        self.assertIn("Decision:", payload["appended_entry"])

    def test_execute_single_direct_tool_call_handles_memory_append_daily_note_duplicate(self) -> None:
        callbacks = self._execution_callbacks()
        callbacks = direct_tool_execution_service.DirectToolExecutionCallbacks(
            **{
                **callbacks.__dict__,
                "memory_append_daily_note": lambda workspace_id, note, agent_install_id=None, actor=None, run_id=None: {
                    "workspace_id": workspace_id,
                    "filename": "memory/2026-05-11.md",
                    "saved": False,
                    "usefulness": "useful",
                    "duplicate_of": "Decision: enforce explicit runtime placement.",
                },
            }
        )
        raw = skills_service.execute_single_direct_tool_call(
            tool_call={
                "name": "memory_append_daily_note",
                "arguments": {"note": "Decision: enforce explicit runtime placement for high-cost actions."},
            },
            workspace_id="default",
            thread_id="thread-1",
            callbacks=callbacks,
        )
        payload = json.loads(raw)
        self.assertFalse(payload["saved"])
        self.assertIn("duplicate_of", payload)

    def test_execute_single_direct_tool_call_dispatches_memory_stage_consolidation(self) -> None:
        raw = skills_service.execute_single_direct_tool_call(
            tool_call={
                "name": "memory_stage_consolidation",
                "arguments": {
                    "proposal": "Decision: consolidate durable preferences into USER.md and procedures into PROCEDURES.md.",
                    "target_files": ["USER.md", "PROCEDURES.md"],
                    "source_refs": ["memory/2026-05-10.md#L2"],
                },
            },
            workspace_id="default",
            thread_id="thread-1",
            callbacks=self._execution_callbacks(),
        )
        payload = json.loads(raw)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["staged_only"])
        self.assertTrue(str(payload["filename"]).startswith("memory/.dreams/"))
        self.assertEqual(payload["target_files"], ["USER.md", "PROCEDURES.md"])

    def test_execute_single_direct_tool_call_dispatches_memory_consolidate_daily_notes(self) -> None:
        raw = skills_service.execute_single_direct_tool_call(
            tool_call={
                "name": "memory_consolidate_daily_notes",
                "arguments": {
                    "target_files": ["MEMORY.md"],
                    "max_notes": 20,
                    "apply_merge": True,
                    "compact_mode": "archive",
                    "user_approved": True,
                    "run_id": "run-1",
                },
            },
            workspace_id="default",
            thread_id="thread-1",
            callbacks=self._execution_callbacks(),
        )
        payload = json.loads(raw)
        self.assertEqual(payload["workspace_id"], "default")
        self.assertTrue(payload["merged"])
        self.assertEqual(payload["audit_id"], "audit-1")

    def test_execute_single_direct_tool_call_dispatches_memory_list_versions(self) -> None:
        raw = skills_service.execute_single_direct_tool_call(
            tool_call={
                "name": "memory_list_versions",
                "arguments": {"filename": "MEMORY.md", "limit": 5},
            },
            workspace_id="default",
            thread_id="thread-1",
            callbacks=self._execution_callbacks(),
        )
        payload = json.loads(raw)
        self.assertEqual(payload["filename"], "MEMORY.md")
        self.assertEqual(payload["versions"][0]["version_id"], "ver-1")

    def test_execute_single_direct_tool_call_dispatches_memory_rollback_version(self) -> None:
        raw = skills_service.execute_single_direct_tool_call(
            tool_call={
                "name": "memory_rollback_version",
                "arguments": {"filename": "MEMORY.md", "version_id": "ver-1", "reason": "requested"},
            },
            workspace_id="default",
            thread_id="thread-1",
            callbacks=self._execution_callbacks(),
        )
        payload = json.loads(raw)
        self.assertEqual(payload["filename"], "MEMORY.md")
        self.assertEqual(payload["rolled_back_to_version_id"], "ver-1")

    def test_build_builtin_direct_chat_tools_includes_sage_service_tools(self) -> None:
        tool_names = {
            item["name"]
            for item in skills_service.build_builtin_direct_chat_tools()
            if isinstance(item, dict)
        }

        self.assertIn("sage_service__list_state", tool_names)
        self.assertIn("sage_service__update_profile", tool_names)
        self.assertIn("sage_service__create_entry", tool_names)
        self.assertIn("memory_update", tool_names)
        self.assertIn("memory_stage_edit", tool_names)
        self.assertIn("memory_apply_edit", tool_names)
        self.assertIn("memory_append_daily_note", tool_names)
        self.assertIn("memory_stage_consolidation", tool_names)
        self.assertIn("memory_consolidate_daily_notes", tool_names)
        self.assertIn("memory_list_versions", tool_names)
        self.assertIn("memory_rollback_version", tool_names)

    def test_execute_single_direct_tool_call_dispatches_sage_service_tools(self) -> None:
        callbacks = self._execution_callbacks()
        callbacks = direct_tool_execution_service.DirectToolExecutionCallbacks(
            **{
                **callbacks.__dict__,
                "run_async_tool_call": lambda awaitable: asyncio.run(awaitable),
            }
        )
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "workspace-1"
            with (
                patch("server_modules.sage_services_service.workspace_context.workspace_scope_dir", return_value=root),
                patch(
                    "server_modules.sage_services_service.personal_context_engine.publish_event",
                    new=AsyncMock(return_value={"id": "evt-1"}),
                ),
            ):
                profile_raw = skills_service.execute_single_direct_tool_call(
                    tool_call={
                        "name": "sage_service__update_profile",
                        "arguments": {
                            "service_id": "language_coach",
                            "profile": {
                                "target_language": "Japanese",
                                "current_level": "A2",
                                "focus_area": "Travel",
                            },
                            "explicit_user_intent": True,
                        },
                    },
                    workspace_id="workspace-1",
                    thread_id="thread-1",
                    callbacks=callbacks,
                    session_ctx={"tenant_id": "tenant-1"},
                )
                entry_raw = skills_service.execute_single_direct_tool_call(
                    tool_call={
                        "name": "sage_service__create_entry",
                        "arguments": {
                            "service_id": "flashcards",
                            "entry": {
                                "deck": "M&A",
                                "front": "SPA",
                                "back": "Share Purchase Agreement",
                            },
                            "explicit_user_intent": True,
                        },
                    },
                    workspace_id="workspace-1",
                    thread_id="thread-1",
                    callbacks=callbacks,
                    session_ctx={"tenant_id": "tenant-1"},
                )
                state_raw = skills_service.execute_single_direct_tool_call(
                    tool_call={
                        "name": "sage_service__list_state",
                        "arguments": {"service_id": "flashcards"},
                    },
                    workspace_id="workspace-1",
                    thread_id="thread-1",
                    callbacks=callbacks,
                    session_ctx={"tenant_id": "tenant-1"},
                )

        profile_payload = json.loads(profile_raw)
        entry_payload = json.loads(entry_raw)
        state_payload = json.loads(state_raw)
        self.assertEqual(profile_payload["profile"]["target_language"], "Japanese")
        self.assertEqual(entry_payload["entries"][0]["front"], "SPA")
        self.assertEqual(state_payload["entries"][0]["back"], "Share Purchase Agreement")

    def test_execute_single_direct_tool_call_routes_safe_local_shell_via_gateway_when_live(self) -> None:
        callbacks = self._execution_callbacks()
        callbacks = direct_tool_execution_service.DirectToolExecutionCallbacks(
            **{
                **callbacks.__dict__,
                "run_async_tool_call": lambda awaitable: asyncio.run(awaitable),
            }
        )
        with (
            patch(
                "server_modules.skills_service._resolve_direct_tool_gateway_id",
                return_value="gw-1",
            ),
            patch(
                "server_modules.skills_service._execute_direct_tool_via_gateway",
                return_value={
                    "gateway_id": "gw-1",
                    "result": {
                        "command": "pwd",
                        "exit_code": 0,
                        "stdout": "/Users/mansur/Multi_Agent_Orchestrator_Project",
                        "stderr": "",
                    },
                },
            ) as execute_gateway_mock,
        ):
            raw = skills_service.execute_single_direct_tool_call(
                tool_call={"name": "shell__exec", "arguments": {"command": "pwd"}},
                workspace_id="default",
                thread_id="thread-1",
                index=1,
                session_ctx={"runtime_id": "gw-1", "request_id": "chat-request-2"},
                callbacks=callbacks,
            )

        self.assertIn("Checked this device.", raw)
        self.assertIn('"command": "pwd"', raw)
        execute_gateway_mock.assert_called_once()
        call_kwargs = execute_gateway_mock.call_args.kwargs
        self.assertEqual(call_kwargs["request_id"], "chat-request-2")
        self.assertEqual(call_kwargs["runtime_access_mode"], "full_access")

    def test_agent_turn_object_policy_context_selects_full_access_for_agent_computer(self) -> None:
        turn_request = AgentTurnRequest(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            thread_id="thread-1",
            session_id="thread-1",
            channel="web",
            actor=TurnActor(type="user", id="user-1", display_name="Mansur"),
            message="run something on this hardware",
            policy_context={"execution_target": "local_companion"},
        )

        self.assertEqual(
            skills_service._runtime_access_mode_from_direct_tool_context(
                session_ctx={"agent_turn_request": turn_request}
            ),
            "full_access",
        )

    def test_execute_single_direct_tool_call_uses_direct_worker_when_gateway_not_live(self) -> None:
        callbacks = self._execution_callbacks()
        workflow_result = {
            "summary": "Executed shell command.",
            "result_data": {
                "child_result": {
                    "outputs": {
                        "actions": [
                            {
                                "tool": "execute_shell_command",
                                "command": "pwd",
                                "stdout_preview": "/tmp/project",
                            }
                        ]
                    }
                }
            },
        }
        with (
            patch(
                "server_modules.skills_service._resolve_direct_tool_gateway_id",
                return_value=None,
            ),
            patch(
                "server_modules.skills_service._execute_direct_tool_via_gateway",
            ) as execute_gateway_mock,
            patch(
                "server_modules.runs_execution._workflow_execute_local_tool",
                return_value=workflow_result,
            ) as execute_local_mock,
        ):
            raw = skills_service.execute_single_direct_tool_call(
                tool_call={"name": "shell__exec", "arguments": {"command": "pwd"}},
                workspace_id="default",
                thread_id="thread-1",
                index=1,
                session_ctx={"request_id": "chat-request-2"},
                callbacks=callbacks,
            )

        self.assertIn("/tmp/project", raw)
        execute_gateway_mock.assert_not_called()
        execute_local_mock.assert_called_once()
        config = execute_local_mock.call_args.args[2]
        self.assertEqual(config["command"], "pwd")
        self.assertEqual(config["execution_target"], "local_companion")

    def test_execute_single_direct_tool_call_uses_local_dev_shell_fallback_without_database(self) -> None:
        callbacks = self._execution_callbacks()
        with (
            patch(
                "server_modules.skills_service._resolve_direct_tool_gateway_id",
                return_value=None,
            ),
            patch(
                "server_modules.skills_service._local_dev_direct_shell_fallback_enabled",
                return_value=True,
            ),
            patch(
                "server_modules.skills_service._execute_local_dev_direct_shell_command",
                return_value="Command completed: pwd\n/tmp/project",
            ) as execute_direct_mock,
            patch(
                "server_modules.runs_execution._workflow_execute_local_tool",
            ) as execute_local_mock,
        ):
            raw = skills_service.execute_single_direct_tool_call(
                tool_call={"name": "shell__exec", "arguments": {"command": "pwd"}},
                workspace_id="default",
                thread_id="thread-1",
                index=1,
                session_ctx={"request_id": "chat-request-2"},
                callbacks=callbacks,
            )

        self.assertIn("/tmp/project", raw)
        execute_direct_mock.assert_called_once()
        execute_local_mock.assert_not_called()

    def test_local_dev_direct_shell_fallback_can_use_default_worker_for_local_workspace(self) -> None:
        worker_payload = {
            "items": [
                {
                    "workspace_id": "default",
                    "online": True,
                    "capabilities": ["shell.execute", "local.worker"],
                }
            ]
        }
        with patch.dict(os.environ, {"ORION_ENV": "local"}, clear=False), patch(
            "server_modules.local_queue.handle_get_local_workers_status",
            return_value=worker_payload,
        ):
            self.assertTrue(skills_service._local_dev_direct_shell_fallback_enabled("ws-1"))

    def test_resolve_direct_tool_gateway_id_falls_back_to_default_only_in_local_env(self) -> None:
        def _registrations(workspace_id, include_revoked=False):
            if workspace_id == "default":
                return [{"gateway_id": "gw-local", "status": "active"}]
            return []

        with patch.dict(os.environ, {"ORION_ENV": "local"}, clear=False), patch(
            "server_modules.gateway_state_repository.list_workspace_gateway_registrations",
            side_effect=_registrations,
        ), patch(
            "server_modules.gateway_protocol_service.gateway_connection_is_live",
            return_value=True,
        ):
            self.assertEqual(
                skills_service._resolve_direct_tool_gateway_id("ws-1", session_ctx={}),
                "gw-local",
            )

        with patch.dict(os.environ, {"ORION_ENV": "production"}, clear=False), patch(
            "server_modules.gateway_state_repository.list_workspace_gateway_registrations",
            side_effect=_registrations,
        ), patch(
            "server_modules.gateway_protocol_service.gateway_connection_is_live",
            return_value=True,
        ):
            self.assertIsNone(skills_service._resolve_direct_tool_gateway_id("ws-1", session_ctx={}))

    def test_execute_single_direct_tool_call_routes_file_read_via_gateway_when_live(self) -> None:
        callbacks = self._execution_callbacks()
        callbacks = direct_tool_execution_service.DirectToolExecutionCallbacks(
            **{
                **callbacks.__dict__,
                "run_async_tool_call": lambda awaitable: asyncio.run(awaitable),
            }
        )
        with (
            patch(
                "server_modules.skills_service._resolve_direct_tool_gateway_id",
                return_value="gw-1",
            ),
            patch(
                "server_modules.skills_service._execute_direct_tool_via_gateway",
                return_value={
                    "gateway_id": "gw-1",
                    "result": {
                        "mode": "read",
                        "path": "/Users/mansur/Desktop",
                        "is_directory": True,
                        "entries": ["a.txt", "b/"],
                    },
                },
            ) as execute_gateway_mock,
        ):
            raw = skills_service.execute_single_direct_tool_call(
                tool_call={"name": "file__read", "arguments": {"path": "/root/Desktop"}},
                workspace_id="default",
                thread_id="thread-1",
                index=1,
                session_ctx={"runtime_id": "gw-1", "request_id": "chat-request-3"},
                callbacks=callbacks,
            )

        self.assertIn("Listed directory: /Users/mansur/Desktop", raw)
        self.assertIn("1. a.txt", raw)
        execute_gateway_mock.assert_called_once()
        call_kwargs = execute_gateway_mock.call_args.kwargs
        self.assertEqual(call_kwargs["capability_id"], "filesystem.read_write")
        self.assertEqual(call_kwargs["request_id"], "chat-request-3")
        self.assertEqual(call_kwargs["arguments"]["path"], str(Path.home() / "Desktop"))
        self.assertEqual(call_kwargs["arguments"]["mode"], "read")

    def test_execute_single_direct_tool_call_routes_hardware_action_through_broker(self) -> None:
        callbacks = self._execution_callbacks()
        callbacks = direct_tool_execution_service.DirectToolExecutionCallbacks(
            **{
                **callbacks.__dict__,
                "run_async_tool_call": lambda awaitable: asyncio.run(awaitable),
            }
        )
        execute_hardware_mock = AsyncMock(
            return_value={
                "status": "offline",
                "reason": "gateway_offline",
                "runtime_session": {
                    "state": "offline",
                    "canonical_runtime_target": "user_device_gateway",
                    "gateway_id": "gw-1",
                },
            }
        )
        with (
            patch(
                "server_modules.skills_service._resolve_direct_tool_gateway_id",
                return_value="gw-1",
            ),
            patch(
                "server_modules.hardware_action_broker_service.execute_hardware_action",
                execute_hardware_mock,
            ),
        ):
            raw = skills_service.execute_single_direct_tool_call(
                tool_call={
                    "name": "hardware__action",
                    "arguments": {
                        "runtime_target": "user_device_gateway",
                        "action": "screenshot.capture",
                        "arguments": {},
                    },
                },
                workspace_id="default",
                thread_id="thread-1",
                index=1,
                session_ctx={
                    "runtime_id": "gw-1",
                    "tenant_id": "tenant-1",
                    "request_id": "chat-request-1",
                    "client_request_id": "chat-request-1",
                },
                callbacks=callbacks,
            )

        payload = json.loads(raw)
        self.assertEqual(payload["status"], "offline")
        self.assertEqual(payload["runtime_target"], "user_device_gateway")
        execute_hardware_mock.assert_awaited_once()
        call_kwargs = execute_hardware_mock.await_args.kwargs
        self.assertEqual(call_kwargs["tenant_id"], "tenant-1")
        self.assertEqual(call_kwargs["runtime_target"], "user_device_gateway")
        self.assertEqual(call_kwargs["action_id"], "screenshot.capture")
        self.assertEqual(call_kwargs["gateway_id"], "gw-1")
        self.assertEqual(call_kwargs["request_id"], "chat-request-1")
        self.assertEqual(call_kwargs["runtime_access_mode"], "full_access")

    def test_execute_single_direct_tool_call_forces_hardware_shell_to_gateway_when_live(self) -> None:
        callbacks = self._execution_callbacks()
        callbacks = direct_tool_execution_service.DirectToolExecutionCallbacks(
            **{
                **callbacks.__dict__,
                "run_async_tool_call": lambda awaitable: asyncio.run(awaitable),
            }
        )
        execute_hardware_mock = AsyncMock(
            return_value={
                "status": "completed",
                "runtime_session": {
                    "state": "completed",
                    "canonical_runtime_target": "user_device_gateway",
                    "gateway_id": "gw-1",
                },
            }
        )
        with (
            patch(
                "server_modules.skills_service._resolve_direct_tool_gateway_id",
                return_value="gw-1",
            ),
            patch(
                "server_modules.hardware_action_broker_service.execute_hardware_action",
                execute_hardware_mock,
            ),
        ):
            raw = skills_service.execute_single_direct_tool_call(
                tool_call={
                    "name": "hardware__action",
                    "arguments": {
                        "action": "shell.execute",
                        "arguments": {"command": "pwd"},
                    },
                },
                workspace_id="default",
                thread_id="thread-1",
                index=1,
                session_ctx={
                    "tenant_id": "tenant-1",
                    "request_id": "chat-request-shell",
                    "client_request_id": "chat-request-shell",
                },
                callbacks=callbacks,
            )

        payload = json.loads(raw)
        self.assertEqual(payload["runtime_target"], "user_device_gateway")
        execute_hardware_mock.assert_awaited_once()
        call_kwargs = execute_hardware_mock.await_args.kwargs
        self.assertEqual(call_kwargs["runtime_target"], "user_device_gateway")
        self.assertEqual(call_kwargs["action_id"], "shell.execute")
        self.assertEqual(call_kwargs["gateway_id"], "gw-1")

    def test_execute_single_direct_tool_call_flattens_hardware_action_data(self) -> None:
        callbacks = self._execution_callbacks()
        callbacks = direct_tool_execution_service.DirectToolExecutionCallbacks(
            **{
                **callbacks.__dict__,
                "run_async_tool_call": lambda awaitable: asyncio.run(awaitable),
            }
        )
        execute_hardware_mock = AsyncMock(
            return_value={
                "status": "completed",
                "runtime_session": {
                    "state": "completed",
                    "canonical_runtime_target": "user_device_gateway",
                    "gateway_id": "gw-1",
                },
            }
        )
        with (
            patch(
                "server_modules.skills_service._resolve_direct_tool_gateway_id",
                return_value="gw-1",
            ),
            patch(
                "server_modules.hardware_action_broker_service.execute_hardware_action",
                execute_hardware_mock,
            ),
        ):
            skills_service.execute_single_direct_tool_call(
                tool_call={
                    "name": "hardware__action",
                    "arguments": {
                        "action": "shell",
                        "action_data": {"command": "echo hello from hardware"},
                    },
                },
                workspace_id="default",
                thread_id="thread-1",
                index=1,
                session_ctx={
                    "tenant_id": "tenant-1",
                    "request_id": "chat-request-shell-action-data",
                    "client_request_id": "chat-request-shell-action-data",
                },
                callbacks=callbacks,
            )

        execute_hardware_mock.assert_awaited_once()
        call_kwargs = execute_hardware_mock.await_args.kwargs
        self.assertEqual(call_kwargs["action_id"], "shell")
        self.assertEqual(call_kwargs["arguments"], {"command": "echo hello from hardware"})
        self.assertEqual(call_kwargs["runtime_target"], "user_device_gateway")

    def test_execute_single_direct_tool_call_hardware_shell_offline_fails_closed(self) -> None:
        callbacks = self._execution_callbacks()
        execute_hardware_mock = AsyncMock(return_value={})
        with (
            patch(
                "server_modules.skills_service._resolve_direct_tool_gateway_id",
                return_value=None,
            ),
            patch(
                "server_modules.hardware_action_broker_service.execute_hardware_action",
                execute_hardware_mock,
            ),
        ):
            raw = skills_service.execute_single_direct_tool_call(
                tool_call={
                    "name": "hardware__action",
                    "arguments": {
                        "action": "shell.execute",
                        "arguments": {"command": "pwd"},
                    },
                },
                workspace_id="default",
                thread_id="thread-1",
                index=1,
                session_ctx={"request_id": "chat-request-shell"},
                callbacks=callbacks,
            )

        payload = json.loads(raw)
        self.assertEqual(payload["status"], "offline")
        self.assertEqual(payload["reason"], "agent_computer_offline")
        self.assertEqual(payload["runtime_target"], "user_device_gateway")
        self.assertEqual(payload["execution_environment"], "local_gateway")
        self.assertIn("Agent Computer offline", payload["summary"])
        execute_hardware_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()

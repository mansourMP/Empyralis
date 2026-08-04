import asyncio
import os
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from server_modules.agent_turn import AgentTurnRequest, TurnActor
from server_modules import agent_capability_service
from server_modules import authority_mandate_service
from server_modules import direct_chat_operator_binding_service
from server_modules import direct_tool_execution_service
from server_modules import no_provider_service
from server_modules import skills_service


class SkillsServiceTests(unittest.TestCase):
    def _execution_callbacks(self) -> direct_tool_execution_service.DirectToolExecutionCallbacks:
        return direct_tool_execution_service.DirectToolExecutionCallbacks(
            compact_step_detail=lambda value: " ".join(str(value or "").split()).strip() or None,
            titleize_direct_step_token=lambda value: " ".join(word.capitalize() for word in str(value or "").split("_")),
            run_async_tool_call=lambda awaitable: awaitable,
            # The real router (not a naive "_"-split lambda): a handful of
            # tool names (generate_image, send_image, the memory_* family)
            # have no "__" separator and need their own explicit mapping —
            # see direct_chat_operator_binding_service.parse_tool_name's own
            # if/elif chain. Using the real function here (rather than a
            # fixture-local approximation) is what makes
            # tool_call={"name": "send_image", ...} / {"name": "generate_image", ...}
            # actually reach the connector_id/action_id branches under test.
            parse_tool_name=direct_chat_operator_binding_service.parse_tool_name,
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
            # Mirrors the real search_memory_notebook envelope (results +
            # files_searched/status/message/errors) so the model can tell a
            # confirmed-empty search from one that never ran.
            search_memory_notebook=lambda workspace_id, query, max_results=5, agent_install_id=None: {
                "results": [{"path": "MEMORY.md", "query": query, "max_results": max_results, "agent_install_id": agent_install_id}],
                "files_searched": 1,
                "errors": [],
                "status": "matches_found",
                "message": "Searched 1 memory file(s).",
            },
            get_memory_notebook_excerpt=lambda workspace_id, rel_path, from_line=None, line_count=None, agent_install_id=None: {
                "path": rel_path,
                "from_line": from_line,
                "line_count": line_count,
                "agent_install_id": agent_install_id,
            },
            update_memory_context_file=lambda workspace_id, filename, content, agent_install_id=None, **kwargs: {
                "workspace_id": workspace_id,
                "filename": filename,
                "content": content,
                "agent_install_id": agent_install_id,
                "version_id": kwargs.get("version_id") or "version-1",
            },
            memory_append_daily_note=lambda workspace_id, note, agent_install_id=None, actor=None, run_id=None, **kwargs: {
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
            session_ctx={"authority_tier": "owner"},
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
            session_ctx={"authority_tier": "owner"},
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
            session_ctx={"authority_tier": "owner"},
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
            session_ctx={"authority_tier": "owner"},
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
                "memory_append_daily_note": lambda workspace_id, note, agent_install_id=None, actor=None, run_id=None, **kwargs: {
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
            session_ctx={"authority_tier": "owner"},
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
            session_ctx={"authority_tier": "owner"},
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
            session_ctx={"authority_tier": "owner"},
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
            session_ctx={"authority_tier": "owner"},
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
                    session_ctx={"tenant_id": "tenant-1", "authority_tier": "owner"},
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
                    session_ctx={"tenant_id": "tenant-1", "authority_tier": "owner"},
                )
                state_raw = skills_service.execute_single_direct_tool_call(
                    tool_call={
                        "name": "sage_service__list_state",
                        "arguments": {"service_id": "flashcards"},
                    },
                    workspace_id="workspace-1",
                    thread_id="thread-1",
                    callbacks=callbacks,
                    session_ctx={"tenant_id": "tenant-1", "authority_tier": "owner"},
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
            # The registration paired to gw-1 is the source of truth for
            # runtime_access_mode, not the mere presence of a gateway_id —
            # so this test grounds "full_access" in a registration that is
            # actually configured (and acknowledged) for it, rather than
            # asserting the access mode falls out of gateway_id alone.
            patch(
                "server_modules.gateway_state_repository.get_gateway_registration",
                return_value={
                    "gateway_id": "gw-1",
                    "metadata": {
                        "runtime_access_mode": "full_access",
                        "autonomous_agent_setup_warning_acknowledged": True,
                    },
                },
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
                session_ctx={"runtime_id": "gw-1", "request_id": "chat-request-2", "authority_tier": "owner"},
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

    def test_runtime_access_mode_from_direct_tool_context_respects_guarded_registration(self) -> None:
        """The regression this whole fix is for: an agent with a
        preferred_gateway_id bound to a real registration must NOT get
        full_access just because a gateway_id is present. The paired
        registration here is genuinely configured default_guarded (and has
        NOT acknowledged the Full Access setup warning) — exactly the
        founder's own gateway registration's real, live configuration — so
        the resolved mode must come back default_guarded, never full_access,
        with no explicit override anywhere in session context."""
        with patch(
            "server_modules.gateway_state_repository.get_gateway_registration",
            return_value={
                "gateway_id": "gateway_a1c6b043",
                "metadata": {
                    "runtime_access_mode": "default_guarded",
                    "autonomous_agent_setup_warning_acknowledged": False,
                },
            },
        ) as get_registration_mock:
            resolved = skills_service._runtime_access_mode_from_direct_tool_context(
                gateway_id="gateway_a1c6b043",
                session_ctx={},
            )

        self.assertEqual(resolved, "default_guarded")
        get_registration_mock.assert_called_once_with("gateway_a1c6b043")

    def test_runtime_access_mode_from_direct_tool_context_respects_full_access_registration(self) -> None:
        """Inverse of the guarded case above: a registration genuinely
        configured for full_access AND with the setup warning acknowledged
        (the SSH-remote-server / cloud-VPS onboarding paths set both) must
        still resolve to full_access — the fix must not flip a global
        default, it must make the registration's own configuration the
        deciding factor in both directions."""
        with patch(
            "server_modules.gateway_state_repository.get_gateway_registration",
            return_value={
                "gateway_id": "gateway_acknowledged_vps",
                "metadata": {
                    "runtime_access_mode": "full_access",
                    "autonomous_agent_setup_warning_acknowledged": True,
                },
            },
        ):
            resolved = skills_service._runtime_access_mode_from_direct_tool_context(
                gateway_id="gateway_acknowledged_vps",
                session_ctx={},
            )

        self.assertEqual(resolved, "full_access")

    def test_runtime_access_mode_from_direct_tool_context_defaults_guarded_for_unknown_gateway(self) -> None:
        """A gateway_id that doesn't resolve to any stored registration
        (stale id, lookup failure, etc.) must fail SAFE to the guarded
        default, never fabricate full_access."""
        with patch(
            "server_modules.gateway_state_repository.get_gateway_registration",
            return_value=None,
        ):
            resolved = skills_service._runtime_access_mode_from_direct_tool_context(
                gateway_id="gateway_does_not_exist",
                session_ctx={},
            )

        self.assertEqual(resolved, "default_guarded")

    def test_runtime_access_mode_from_direct_tool_context_explicit_override_still_wins(self) -> None:
        """An explicit mode passed by the caller (e.g. the agent's own
        configured execution_mode) still short-circuits the registration
        lookup entirely, exactly as before this fix."""
        with patch(
            "server_modules.gateway_state_repository.get_gateway_registration",
        ) as get_registration_mock:
            resolved = skills_service._runtime_access_mode_from_direct_tool_context(
                explicit_mode="approval_mode",
                gateway_id="gateway_a1c6b043",
                session_ctx={},
            )

        self.assertEqual(resolved, "default_guarded")
        get_registration_mock.assert_not_called()

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
                session_ctx={"request_id": "chat-request-2", "authority_tier": "owner"},
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
                session_ctx={"request_id": "chat-request-2", "authority_tier": "owner"},
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
                session_ctx={"runtime_id": "gw-1", "request_id": "chat-request-3", "authority_tier": "owner"},
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
            # As above: ground "full_access" in gw-1's own registration
            # being configured (and acknowledged) for it, not in gateway_id
            # merely being present.
            patch(
                "server_modules.gateway_state_repository.get_gateway_registration",
                return_value={
                    "gateway_id": "gw-1",
                    "metadata": {
                        "runtime_access_mode": "full_access",
                        "autonomous_agent_setup_warning_acknowledged": True,
                    },
                },
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
                    "authority_tier": "owner",
                },
                callbacks=callbacks,
            )

        payload = json.loads(raw)
        self.assertEqual(payload["status"], "offline")
        self.assertEqual(payload["runtime_target"], "user_device_gateway")
        # MAN-295: the JSON string returned here IS the tool result content a
        # model reads directly on this path (see skills_service._format_
        # hardware_action_result) — it must carry plain-language guidance
        # alongside the raw "gateway_offline" reason token, not just the
        # token itself.
        self.assertEqual(
            payload["message"],
            "This computer isn't connected right now. Make sure it's powered on and the "
            "Empyralis gateway is running, then retry.",
        )
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
                    "authority_tier": "owner",
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
                    "authority_tier": "owner",
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
                session_ctx={"request_id": "chat-request-shell", "authority_tier": "owner"},
                callbacks=callbacks,
            )

        payload = json.loads(raw)
        self.assertEqual(payload["status"], "offline")
        self.assertEqual(payload["reason"], "agent_computer_offline")
        self.assertEqual(payload["runtime_target"], "user_device_gateway")
        self.assertEqual(payload["execution_environment"], "local_gateway")
        self.assertIn("Agent Computer offline", payload["summary"])
        execute_hardware_mock.assert_not_called()


class GenerateImageCapabilityResolutionTests(unittest.TestCase):
    """execute_single_direct_tool_call's image/generate branch — must resolve
    THIS agent's capability provider (not read platform env keys blindly),
    route the resolved provider's key into tools_image_gen.generate_image,
    and meter platform_credits usage. Uses the REAL parse_tool_name (not
    SkillsServiceTests._execution_callbacks' simplified stub, which doesn't
    special-case "generate_image" -> ("image", "generate") the way
    direct_chat_operator_binding_service.parse_tool_name does) and a REAL
    run_async_tool_call bridge (asyncio.run) since this code path, unlike
    the fire-and-forget ledger writes most other tests exercise, actually
    consumes the coroutine's return value."""

    def _callbacks(self, **overrides) -> direct_tool_execution_service.DirectToolExecutionCallbacks:
        from server_modules.direct_chat_operator_binding_service import parse_tool_name as _real_parse_tool_name

        base = dict(
            compact_step_detail=lambda v: None,
            titleize_direct_step_token=lambda v: str(v or ""),
            run_async_tool_call=lambda coro: asyncio.run(coro),
            parse_tool_name=_real_parse_tool_name,
            tool_arguments_payload=lambda a: dict(a) if isinstance(a, dict) else {},
            parse_json_object_loose=lambda s: {},
            safe_positive_int=lambda v, d=0: d,
            normalize_reasoning_effort=lambda s: None,
            build_direct_local_tool_config=lambda c, a, args: ("", {}),
            format_direct_local_tool_result=lambda r: str(r),
            build_direct_tool_config=lambda c, a, i: {},
            format_direct_tool_result=lambda r: str(r),
            llm_task=lambda *a, **k: None,
            web_search=lambda q: [],
            web_fetch=lambda u: "",
            search_memory_notebook=lambda *a, **k: None,
            get_memory_notebook_excerpt=lambda *a, **k: None,
        )
        base.update(overrides)
        return direct_tool_execution_service.DirectToolExecutionCallbacks(**base)

    def test_resolves_per_agent_provider_and_threads_the_key_through(self):
        resolution = agent_capability_service.CapabilityResolution(
            capability="image_generation", available=True, mode="byok_api",
            provider="openai", credentials={"api_key": "sk-agent-own-key"}, billing_mode="byok_api",
        )
        with (
            patch(
                "server_modules.agent_capability_service.resolve_agent_capability_provider_by_id",
                new=AsyncMock(return_value=resolution),
            ),
            patch("server_modules.tools_image_gen.generate_image", return_value=["/tmp/out.png"]) as mock_generate,
            patch("server_modules.activity_ledger_service.append_execution_activity", new=AsyncMock()),
        ):
            result = skills_service.execute_single_direct_tool_call(
                tool_call={"name": "generate_image", "arguments": {"prompt": "a red fox"}},
                workspace_id="ws-1", thread_id="thread-1",
                session_ctx={"agent_id": "agent-x", "tenant_id": "t1", "authority_tier": "owner"},
                callbacks=self._callbacks(),
            )
        self.assertIn("Generated 1 image(s)", result)
        _, kwargs = mock_generate.call_args
        self.assertEqual(kwargs["api_key"], "sk-agent-own-key")
        self.assertEqual(kwargs["model"], "dall-e-3")

    def test_stability_provider_forces_stable_diffusion_model_regardless_of_llm_request(self):
        """The resolved provider is the source of truth for which backend
        serves the call — a stability-configured agent must never silently
        route to OpenAI just because the tool schema's model enum defaults
        (or the LLM explicitly asked for) dall-e-3."""
        resolution = agent_capability_service.CapabilityResolution(
            capability="image_generation", available=True, mode="platform_credits",
            provider="stability", credentials={"api_key": "sk-platform-stability"}, billing_mode="platform_credits",
        )
        with (
            patch(
                "server_modules.agent_capability_service.resolve_agent_capability_provider_by_id",
                new=AsyncMock(return_value=resolution),
            ),
            patch("server_modules.tools_image_gen.generate_image", return_value=["/tmp/out.png"]) as mock_generate,
            patch("server_modules.agent_capability_service.meter_platform_capability_usage", new=AsyncMock()) as mock_meter,
            patch("server_modules.activity_ledger_service.append_execution_activity", new=AsyncMock()),
        ):
            skills_service.execute_single_direct_tool_call(
                tool_call={"name": "generate_image", "arguments": {"prompt": "x", "model": "dall-e-3"}},
                workspace_id="ws-1", thread_id="thread-1",
                session_ctx={"agent_id": "agent-x", "tenant_id": "t1", "authority_tier": "owner"},
                callbacks=self._callbacks(),
            )
        _, kwargs = mock_generate.call_args
        self.assertEqual(kwargs["model"], "stable-diffusion")
        self.assertEqual(kwargs["api_key"], "sk-platform-stability")
        # platform_credits billing_mode -> usage gets metered.
        mock_meter.assert_awaited_once()
        _, meter_kwargs = mock_meter.call_args
        self.assertEqual(meter_kwargs["provider"], "stability")
        self.assertEqual(meter_kwargs["agent_id"], "agent-x")

    def test_byok_calls_are_never_metered(self):
        resolution = agent_capability_service.CapabilityResolution(
            capability="image_generation", available=True, mode="byok_api",
            provider="openai", credentials={"api_key": "sk-agent-own"}, billing_mode="byok_api",
        )
        with (
            patch(
                "server_modules.agent_capability_service.resolve_agent_capability_provider_by_id",
                new=AsyncMock(return_value=resolution),
            ),
            patch("server_modules.tools_image_gen.generate_image", return_value=["/tmp/out.png"]),
            patch("server_modules.agent_capability_service.meter_platform_capability_usage", new=AsyncMock()) as mock_meter,
            patch("server_modules.activity_ledger_service.append_execution_activity", new=AsyncMock()),
        ):
            skills_service.execute_single_direct_tool_call(
                tool_call={"name": "generate_image", "arguments": {"prompt": "x"}},
                workspace_id="ws-1", thread_id="thread-1",
                session_ctx={"agent_id": "agent-x", "tenant_id": "t1", "authority_tier": "owner"},
                callbacks=self._callbacks(),
            )
        mock_meter.assert_not_awaited()

    def test_unresolved_capability_raises_a_clean_actionable_error_not_a_platform_key_exception(self):
        resolution = agent_capability_service.CapabilityResolution(
            capability="image_generation", available=False, mode="byok_api", provider="openai",
            credentials={}, billing_mode="", reason="byok_key_missing",
            message="Add your OpenAI (DALL-E) API key for Image generation.",
        )
        with (
            patch(
                "server_modules.agent_capability_service.resolve_agent_capability_provider_by_id",
                new=AsyncMock(return_value=resolution),
            ),
            patch("server_modules.tools_image_gen.generate_image") as mock_generate,
            patch("server_modules.activity_ledger_service.append_execution_activity", new=AsyncMock()),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                skills_service.execute_single_direct_tool_call(
                    tool_call={"name": "generate_image", "arguments": {"prompt": "x"}},
                    workspace_id="ws-1", thread_id="thread-1",
                    session_ctx={"agent_id": "agent-x", "tenant_id": "t1", "authority_tier": "owner"},
                    callbacks=self._callbacks(),
                )
        self.assertIn("Add your OpenAI", str(ctx.exception))
        mock_generate.assert_not_called()

    def test_sage_own_turn_resolves_with_empty_agent_id_not_specialist_bleed(self):
        """No agent_id in session_ctx == Sage's own turn (matches
        sage_agent_runtime_service._acting_install_id's convention) — must
        resolve against Sage's OWN capability_config via the master-install
        path, never a specialist's."""
        resolution = agent_capability_service.CapabilityResolution(
            capability="image_generation", available=True, mode="platform_credits",
            provider="openai", credentials={"api_key": "sk-platform"}, billing_mode="platform_credits",
        )
        captured_agent_id = {}

        async def _fake_resolve(*, workspace_id, tenant_id, agent_id, capability):
            captured_agent_id["value"] = agent_id
            return resolution

        with (
            patch(
                "server_modules.agent_capability_service.resolve_agent_capability_provider_by_id",
                new=_fake_resolve,
            ),
            patch("server_modules.tools_image_gen.generate_image", return_value=["/tmp/out.png"]),
            patch("server_modules.agent_capability_service.meter_platform_capability_usage", new=AsyncMock()),
            patch("server_modules.activity_ledger_service.append_execution_activity", new=AsyncMock()),
        ):
            skills_service.execute_single_direct_tool_call(
                tool_call={"name": "generate_image", "arguments": {"prompt": "x"}},
                workspace_id="ws-1", thread_id="thread-1",
                session_ctx={"authority_tier": "owner"},  # no agent_id at all -> Sage's own turn
                callbacks=self._callbacks(),
            )
        self.assertEqual(captured_agent_id["value"], "")
class SendImageAndAutoAttachTests(unittest.TestCase):
    """send_image's execution handler and generate_image's channel-context
    auto-attach — the agent-decision layer that turns a generated/local file
    into an actual outbound attachment. Both write into session_ctx's shared
    "pending_outbound_media" list (see _run_sage_action_loop_v3's session_ctx
    construction and handle_sage_chat's "media" response key), which is the
    same dict object the caller still holds afterward — CPython threads
    share memory, so a mutation made deep inside execute_single_direct_tool_call
    (itself invoked via a ThreadPoolExecutor hop from
    direct_chat_generation_service.py) is visible to the caller without any
    return-value plumbing. These tests assert directly on that mutation.
    """

    def _execution_callbacks(self, **overrides) -> direct_tool_execution_service.DirectToolExecutionCallbacks:
        # run_async_tool_call is a lazy passthrough (not asyncio.run) by
        # default: most tests in this class (send_image) never route through
        # it for anything the assertions depend on, and staying lazy avoids
        # ever actually executing the unconditional activity-ledger append
        # near the top of execute_single_direct_tool_call. The
        # generate_image tests below DO need a real event loop — the
        # capabilities feature (agent_capability_service resolution) gates
        # generate_image behind an awaited coroutine now — so those pass
        # run_async_tool_call=lambda coro: asyncio.run(coro) as an override,
        # same as GenerateImageCapabilityResolutionTests._callbacks above.
        base = dict(
            compact_step_detail=lambda value: " ".join(str(value or "").split()).strip() or None,
            titleize_direct_step_token=lambda value: " ".join(word.capitalize() for word in str(value or "").split("_")),
            run_async_tool_call=lambda awaitable: awaitable,
            parse_tool_name=direct_chat_operator_binding_service.parse_tool_name,
            tool_arguments_payload=lambda payload: payload if isinstance(payload, dict) else {},
            parse_json_object_loose=lambda value: {},
            safe_positive_int=lambda value, default=0: int(value) if str(value or "").strip().isdigit() else default,
            normalize_reasoning_effort=lambda value: str(value or "").strip().lower() or None,
            build_direct_local_tool_config=skills_service.build_direct_local_tool_config,
            format_direct_local_tool_result=lambda result: json.dumps(result, ensure_ascii=False),
            build_direct_tool_config=lambda connector_id, action_id, tool_input: {
                "connector": connector_id, "action": action_id, "input": tool_input,
            },
            format_direct_tool_result=lambda result: json.dumps(result, ensure_ascii=False),
            llm_task=lambda *args, **kwargs: {"ok": True},
            web_search=lambda query: [],
            web_fetch=lambda url: f"Fetched {url}",
            search_memory_notebook=lambda *a, **k: [],
            get_memory_notebook_excerpt=lambda *a, **k: {},
            update_memory_context_file=lambda *a, **k: {},
            memory_append_daily_note=lambda *a, **k: {},
            create_memory_consolidation_staging_file=lambda *a, **k: {},
            consolidate_daily_memory_notes=lambda *a, **k: {},
            apply_memory_consolidation_staging=lambda *a, **k: {},
            list_memory_file_versions=lambda *a, **k: [],
            rollback_memory_file_version=lambda *a, **k: {},
        )
        base.update(overrides)
        return direct_tool_execution_service.DirectToolExecutionCallbacks(**base)

    def _channel_session_ctx(self, channel_origin: str = "whatsapp_personal") -> dict:
        # authority_tier is required here, not just realism: _authority_mandate_gate
        # (skills_service.py) fail-closes to "audience" for any session_ctx
        # missing it, and neither send_image nor generate_image is
        # audience_safe — an owner-tier turn is what _run_sage_action_loop_v3
        # actually stamps for every live channel (see that gate's own
        # docstring), so this matches production, not a test-only shortcut.
        return {"metadata": {"channel_origin": channel_origin}, "authority_tier": "owner"}

    # ── send_image: local path ──────────────────────────────────────────

    def test_send_image_local_path_queues_media_with_caption(self) -> None:
        callbacks = self._execution_callbacks()
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                image_dir = Path(tmpdir) / ".orion-stack" / "generated_images"
                image_dir.mkdir(parents=True, exist_ok=True)
                image_path = image_dir / "receipt.png"
                image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-png-bytes")

                session_ctx = self._channel_session_ctx("whatsapp_personal")
                result = skills_service.execute_single_direct_tool_call(
                    tool_call={
                        "name": "send_image",
                        "arguments": {"path_or_url": str(image_path), "caption": "Here's the receipt"},
                    },
                    workspace_id="default",
                    thread_id="thread-1",
                    session_ctx=session_ctx,
                    callbacks=callbacks,
                )
            finally:
                os.chdir(cwd)

        self.assertIn("Queued", result)
        queued = session_ctx["pending_outbound_media"]
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["kind"], "image")
        # Compare against the RESOLVED path, not the literal input: on macOS
        # tempfile.TemporaryDirectory() returns a /var/... path that's itself
        # a symlink to /private/var/..., and _resolve_send_image_local_path
        # deliberately calls Path.resolve() (that's the containment check's
        # own security property, not an artifact to work around).
        self.assertEqual(queued[0]["source_path"], str(image_path.resolve()))
        self.assertEqual(queued[0]["caption"], "Here's the receipt")
        self.assertEqual(queued[0]["mime_type"], "image/png")

    def test_send_image_rejects_path_outside_safe_root(self) -> None:
        """The core security property: send_image must not become an
        arbitrary-file-read/exfiltration primitive. A path outside
        .orion-stack/ (e.g. an attacker- or prompt-injection-supplied
        ~/.ssh/id_rsa or /app/.env) must be rejected, not read and queued
        for delivery to whoever is on the other end of the chat."""
        callbacks = self._execution_callbacks()
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                outside_dir = Path(tmpdir) / "not-orion-stack"
                outside_dir.mkdir(parents=True, exist_ok=True)
                secret_path = outside_dir / "secret.txt"
                secret_path.write_text("super secret content")

                session_ctx = self._channel_session_ctx("whatsapp_personal")
                with self.assertRaises(ValueError) as ctx:
                    skills_service.execute_single_direct_tool_call(
                        tool_call={
                            "name": "send_image",
                            "arguments": {"path_or_url": str(secret_path)},
                        },
                        workspace_id="default",
                        thread_id="thread-1",
                        session_ctx=session_ctx,
                        callbacks=callbacks,
                    )
            finally:
                os.chdir(cwd)

        self.assertIn(".orion-stack", str(ctx.exception))
        self.assertNotIn("pending_outbound_media", session_ctx)

    def test_send_image_rejects_path_traversal_out_of_safe_root(self) -> None:
        callbacks = self._execution_callbacks()
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                (Path(tmpdir) / ".orion-stack").mkdir(parents=True, exist_ok=True)
                outside_file = Path(tmpdir) / "outside.txt"
                outside_file.write_text("nope")

                session_ctx = self._channel_session_ctx("whatsapp_personal")
                with self.assertRaises(ValueError):
                    skills_service.execute_single_direct_tool_call(
                        tool_call={
                            "name": "send_image",
                            "arguments": {"path_or_url": ".orion-stack/../outside.txt"},
                        },
                        workspace_id="default",
                        thread_id="thread-1",
                        session_ctx=session_ctx,
                        callbacks=callbacks,
                    )
            finally:
                os.chdir(cwd)

    def test_send_image_missing_local_file_raises(self) -> None:
        callbacks = self._execution_callbacks()
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                session_ctx = self._channel_session_ctx("whatsapp_personal")
                with self.assertRaises(ValueError) as ctx:
                    skills_service.execute_single_direct_tool_call(
                        tool_call={
                            "name": "send_image",
                            "arguments": {"path_or_url": ".orion-stack/generated_images/nope.png"},
                        },
                        workspace_id="default",
                        thread_id="thread-1",
                        session_ctx=session_ctx,
                        callbacks=callbacks,
                    )
            finally:
                os.chdir(cwd)
        self.assertIn("not found", str(ctx.exception))

    # ── send_image: URL ──────────────────────────────────────────────────

    def test_send_image_url_queues_media_without_touching_disk(self) -> None:
        callbacks = self._execution_callbacks()
        session_ctx = self._channel_session_ctx("telegram_personal")
        result = skills_service.execute_single_direct_tool_call(
            tool_call={
                "name": "send_image",
                "arguments": {"path_or_url": "https://example.com/cat.jpg"},
            },
            workspace_id="default",
            thread_id="thread-1",
            session_ctx=session_ctx,
            callbacks=callbacks,
        )

        self.assertIn("Queued", result)
        queued = session_ctx["pending_outbound_media"]
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["kind"], "image")
        self.assertEqual(queued[0]["source_url"], "https://example.com/cat.jpg")
        self.assertNotIn("source_path", queued[0])

    # ── _queue_outbound_media: the shared accumulator helper ────────────
    #
    # Exercised directly rather than through execute_single_direct_tool_call:
    # a session_ctx that isn't a dict (the "no turn to attach to" case) is
    # indistinguishable, from the authority-mandate gate's point of view,
    # from an unattributed/audience-tier caller — _authority_mandate_gate
    # fail-closes and raises its own "workspace owner only" RuntimeError
    # before send_image's handler ever runs. So the only way to reach a real
    # session_ctx that IS a dict is one _run_sage_action_loop_v3 already
    # stamped, which always includes "pending_outbound_media" — meaning
    # _queue_outbound_media's False branch is a pure defensive backstop for
    # this call path, not something send_image's own dispatch can trigger
    # end-to-end. Unit-testing the helper directly covers it precisely.

    def test_queue_outbound_media_returns_false_without_a_session(self) -> None:
        self.assertFalse(skills_service._queue_outbound_media(None, {"kind": "image"}))
        self.assertFalse(skills_service._queue_outbound_media("not-a-dict", {"kind": "image"}))

    def test_queue_outbound_media_appends_in_place(self) -> None:
        session_ctx: dict = {}
        self.assertTrue(skills_service._queue_outbound_media(session_ctx, {"kind": "image", "source_url": "https://x/1.png"}))
        self.assertTrue(skills_service._queue_outbound_media(session_ctx, {"kind": "image", "source_url": "https://x/2.png"}))
        self.assertEqual(
            session_ctx["pending_outbound_media"],
            [{"kind": "image", "source_url": "https://x/1.png"}, {"kind": "image", "source_url": "https://x/2.png"}],
        )

    def test_send_image_requires_path_or_url(self) -> None:
        callbacks = self._execution_callbacks()
        with self.assertRaises(RuntimeError):
            skills_service.execute_single_direct_tool_call(
                tool_call={"name": "send_image", "arguments": {}},
                workspace_id="default",
                thread_id="thread-1",
                session_ctx=self._channel_session_ctx(),
                callbacks=callbacks,
            )

    # ── generate_image auto-attach ───────────────────────────────────────

    def _generate_image_callbacks(self) -> direct_tool_execution_service.DirectToolExecutionCallbacks:
        # generate_image's handler resolves this agent's capability provider
        # via callbacks.run_async_tool_call(<coroutine>) before it does
        # anything else (see skills_service.execute_single_direct_tool_call's
        # "image"/"generate" branch) — needs a real event loop, unlike this
        # class's other tests (send_image never awaits anything).
        return self._execution_callbacks(run_async_tool_call=lambda coro: asyncio.run(coro))

    def _mock_available_image_capability(self):
        return patch(
            "server_modules.agent_capability_service.resolve_agent_capability_provider_by_id",
            new=AsyncMock(return_value=agent_capability_service.CapabilityResolution(
                capability="image_generation", available=True, mode="byok_api",
                provider="openai", credentials={"api_key": "sk-test"}, billing_mode="byok_api",
            )),
        )

    def test_generate_image_auto_attaches_in_channel_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "generated.png"
            with (
                patch(
                    "server_modules.tools_image_gen.generate_image",
                    return_value=[str(output_path)],
                ),
                self._mock_available_image_capability(),
                patch("server_modules.activity_ledger_service.append_execution_activity", new=AsyncMock()),
            ):
                callbacks = self._generate_image_callbacks()
                session_ctx = self._channel_session_ctx("whatsapp_personal")
                result = skills_service.execute_single_direct_tool_call(
                    tool_call={
                        "name": "generate_image",
                        "arguments": {"prompt": "a red fox"},
                    },
                    workspace_id="default",
                    thread_id="thread-1",
                    session_ctx=session_ctx,
                    callbacks=callbacks,
                )

        self.assertIn("Generated 1 image(s)", result)
        self.assertIn("queued to send", result)
        queued = session_ctx["pending_outbound_media"]
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0], {
            "kind": "image",
            "source_path": str(output_path),
            "mime_type": "image/png",
        })

    def test_generate_image_does_not_auto_attach_outside_channel_context(self) -> None:
        """Web chat / dashboard turns (channel_origin absent or "sage") have
        no channel to attach an image TO — generate_image must behave exactly
        as before there: just report the saved path, nothing queued."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "generated.png"
            with (
                patch(
                    "server_modules.tools_image_gen.generate_image",
                    return_value=[str(output_path)],
                ),
                self._mock_available_image_capability(),
                patch("server_modules.activity_ledger_service.append_execution_activity", new=AsyncMock()),
            ):
                callbacks = self._generate_image_callbacks()
                session_ctx = {"metadata": {"channel_origin": "sage"}, "authority_tier": "owner"}
                result = skills_service.execute_single_direct_tool_call(
                    tool_call={
                        "name": "generate_image",
                        "arguments": {"prompt": "a red fox"},
                    },
                    workspace_id="default",
                    thread_id="thread-1",
                    session_ctx=session_ctx,
                    callbacks=callbacks,
                )

        self.assertIn("Generated 1 image(s)", result)
        self.assertNotIn("queued to send", result)
        self.assertNotIn("pending_outbound_media", session_ctx)

    def test_generate_image_auto_attach_caps_at_max_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = [str(Path(tmpdir) / f"img-{i}.png") for i in range(6)]
            with (
                patch(
                    "server_modules.tools_image_gen.generate_image",
                    return_value=paths,
                ),
                self._mock_available_image_capability(),
                patch("server_modules.activity_ledger_service.append_execution_activity", new=AsyncMock()),
            ):
                callbacks = self._generate_image_callbacks()
                session_ctx = self._channel_session_ctx("whatsapp_personal")
                skills_service.execute_single_direct_tool_call(
                    tool_call={
                        "name": "generate_image",
                        "arguments": {"prompt": "six cats", "n": 4},
                    },
                    workspace_id="default",
                    thread_id="thread-1",
                    session_ctx=session_ctx,
                    callbacks=callbacks,
                )

        self.assertEqual(len(session_ctx["pending_outbound_media"]), skills_service._MAX_AUTO_ATTACH_IMAGES)


class SendImageToolDescriptorTests(unittest.TestCase):
    def test_send_image_descriptor_accepts_local_path_or_url(self) -> None:
        descriptors = skills_service._builtin_tool_descriptors()
        send_image = next(d for d in descriptors if d.tool_name == "send_image")
        self.assertEqual(send_image.connector_id, "messaging")
        self.assertEqual(send_image.action_id, "send_image")
        self.assertIn("path_or_url", send_image.parameters["properties"])
        self.assertIn("path_or_url", send_image.parameters["required"])
        self.assertNotIn(
            "publicly accessible",
            send_image.description,
            "description should no longer demand a publicly-hosted URL now that local paths are accepted",
        )


class AuthorityMandateGateTests(unittest.TestCase):
    """Phase-Mandate: execution-time enforcement in the tool-execution choke
    point (skills_service.execute_single_direct_tool_call{,_async}) — the
    hard backstop behind audience_tool_filter's visibility-only filter.
    """

    def _callbacks(self) -> direct_tool_execution_service.DirectToolExecutionCallbacks:
        return direct_tool_execution_service.DirectToolExecutionCallbacks(
            compact_step_detail=lambda value: None,
            titleize_direct_step_token=lambda value: str(value or ""),
            run_async_tool_call=lambda awaitable: asyncio.run(awaitable),
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
            search_memory_notebook=lambda workspace_id, query, max_results=5, agent_install_id=None: [
                {"path": "MEMORY.md", "query": query, "max_results": max_results, "agent_install_id": agent_install_id}
            ],
            get_memory_notebook_excerpt=lambda workspace_id, rel_path, from_line=None, line_count=None, agent_install_id=None: {},
        )

    def test_audience_tier_blocks_non_audience_safe_tool_even_when_not_visibility_filtered(self) -> None:
        """shell__exec is never audience_safe. Calling execute_single_direct_tool_call
        directly (bypassing audience_tool_filter entirely, as if the visibility
        filter had been skipped or a stale manifest let it through) must still
        block for an audience-tier caller."""
        with self.assertRaises(RuntimeError) as ctx:
            skills_service.execute_single_direct_tool_call(
                tool_call={"name": "shell__exec", "arguments": {"command": "echo hi"}},
                workspace_id="ws-1",
                thread_id="thread-1",
                index=1,
                session_ctx={"authority_tier": "audience"},
                callbacks=self._callbacks(),
            )
        self.assertEqual(str(ctx.exception), authority_mandate_service.MANDATE_BLOCKED_MESSAGE)

    def test_owner_tier_reaches_real_dispatch(self) -> None:
        """Owner tier bypasses the mandate gate — proceeds to the same
        local-dev dispatch as before the mandate existed. Forces the
        deterministic in-process shortcut (rather than the gateway path,
        which depends on environment state this test shouldn't couple to)
        so the assertion is about the mandate gate, not about dispatch
        routing."""
        with (
            patch("server_modules.local_tool_executor.is_local_dev", return_value=True),
            patch("server_modules.skills_service._local_direct_shell_worker_online_exact", return_value=True),
            patch(
                "server_modules.local_tool_executor.shell_execute",
                return_value={"command": "echo hi", "exit_code": 0, "stdout": "hi", "stderr": ""},
            ) as shell_execute_mock,
        ):
            raw = skills_service.execute_single_direct_tool_call(
                tool_call={"name": "shell__exec", "arguments": {"command": "echo hi"}},
                workspace_id="ws-1",
                thread_id="thread-1",
                index=1,
                session_ctx={"authority_tier": "owner"},
                callbacks=self._callbacks(),
            )
        shell_execute_mock.assert_called_once()
        self.assertIn('"command": "echo hi"', raw)

    def test_missing_authority_tier_key_fails_closed_to_audience(self) -> None:
        """A session_ctx with no authority_tier key at all is FAIL-CLOSED,
        not a pass-through: it's treated as audience (normalize_tier(None)'s
        own fail-safe), so a non-audience_safe tool is blocked exactly as it
        would be for an explicitly-audience-tier caller. Flipped from the
        prior skip-enforcement behavior once the mandate hardening report
        confirmed every live tier-stamping producer always stamps a tier —
        a call site reaching this gate with the key missing is either a
        genuine gap (see mandate_unattributed ledgering) or dead code, never
        a caller this default needs to protect."""
        with self.assertRaises(RuntimeError) as ctx:
            skills_service.execute_single_direct_tool_call(
                tool_call={"name": "shell__exec", "arguments": {"command": "echo hi"}},
                workspace_id="ws-1",
                thread_id="thread-1",
                index=1,
                session_ctx={},
                callbacks=self._callbacks(),
            )
        self.assertEqual(str(ctx.exception), authority_mandate_service.MANDATE_BLOCKED_MESSAGE)

    def test_missing_authority_tier_key_still_allows_audience_safe_tool(self) -> None:
        """The fail-closed default only restricts non-audience_safe tools —
        an audience_safe tool (e.g. memory_search) still dispatches normally
        even with no authority_tier key, same as an explicit audience tier
        would allow."""
        raw = skills_service.execute_single_direct_tool_call(
            tool_call={"name": "memory_search", "arguments": {"query": "goals"}},
            workspace_id="ws-1",
            thread_id="thread-1",
            index=1,
            session_ctx={},
            callbacks=self._callbacks(),
        )
        self.assertIn("MEMORY.md", raw)

    def test_audience_tier_allows_audience_safe_tool(self) -> None:
        """memory_search is audience_safe=True (part of the customer_facing
        preset's 8-tool set) — an audience-tier caller may call it. This is
        also a regression guard for tool_name resolution: memory_search does
        NOT follow the connector__action double-underscore convention most
        other tools use, so the gate must resolve it by raw tool_name, not
        only via the connector_id/action_id reconstruction."""
        raw = skills_service.execute_single_direct_tool_call(
            tool_call={"name": "memory_search", "arguments": {"query": "hello"}},
            workspace_id="ws-1",
            thread_id="thread-1",
            index=1,
            session_ctx={"authority_tier": "audience"},
            callbacks=self._callbacks(),
        )
        self.assertIn("MEMORY.md", raw)

    def test_async_entrypoint_blocks_hardware_bound_connector_for_audience_tier(self) -> None:
        """execute_single_direct_tool_call_async handles hardware/file/shell/
        screenshot/computer on its own branch rather than always delegating
        to the sync function — the gate must be checked there too."""

        async def _run() -> str:
            return await skills_service.execute_single_direct_tool_call_async(
                tool_call={"name": "shell__exec", "arguments": {"command": "echo hi"}},
                workspace_id="ws-1",
                thread_id="thread-1",
                index=1,
                session_ctx={"authority_tier": "audience"},
                callbacks=self._callbacks(),
            )

        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(_run())
        self.assertEqual(str(ctx.exception), authority_mandate_service.MANDATE_BLOCKED_MESSAGE)

    def test_schedule_task_owner_tier_wake_request_carries_owner_tier(self) -> None:
        """fleet__schedule_task, now wired into the live dispatcher: an
        owner-tier caller's wake request is persisted with authority_tier
        'owner' (via inherit_tier, not re-derived)."""
        with patch(
            "server_modules.bounded_scheduler_service.propose_self_wakeup",
            new=AsyncMock(return_value={"accepted": True, "wake_request": {"id": "wake-1"}}),
        ) as propose_mock:
            raw = skills_service.execute_single_direct_tool_call(
                tool_call={
                    "name": "fleet__schedule_task",
                    "arguments": {"agent_id": "agent-x", "when": "in 30 minutes", "instruction": "Follow up"},
                },
                workspace_id="ws-1",
                thread_id="thread-1",
                index=1,
                session_ctx={"authority_tier": "owner"},
                callbacks=self._callbacks(),
            )
        result = json.loads(raw)
        self.assertTrue(result["ok"])
        self.assertEqual(propose_mock.call_args.kwargs["payload"]["authority_tier"], "owner")

    def test_schedule_task_audience_tier_blocked_by_default(self) -> None:
        """An end customer must not be able to schedule future agent work —
        fleet__schedule_task is audience_safe=False and no mandate override
        is set."""
        with self.assertRaises(RuntimeError) as ctx:
            skills_service.execute_single_direct_tool_call(
                tool_call={
                    "name": "fleet__schedule_task",
                    "arguments": {"agent_id": "agent-x", "when": "in 30 minutes", "instruction": "Follow up"},
                },
                workspace_id="ws-1",
                thread_id="thread-1",
                index=1,
                session_ctx={"authority_tier": "audience"},
                callbacks=self._callbacks(),
            )
        self.assertEqual(str(ctx.exception), authority_mandate_service.MANDATE_BLOCKED_MESSAGE)

    def test_schedule_task_audience_tier_allowed_when_owner_lists_it_in_mandate(self) -> None:
        """The owner opts a specific agent into letting audience-tier callers
        schedule work by listing 'fleet.schedule_task' in that agent's
        mandate.audience_tools — the resulting wake request still carries
        the audience tier (never upgraded), so its later execution stays
        audience end-to-end."""
        with patch(
            "server_modules.bounded_scheduler_service.propose_self_wakeup",
            new=AsyncMock(return_value={"accepted": True, "wake_request": {"id": "wake-2"}}),
        ) as propose_mock:
            raw = skills_service.execute_single_direct_tool_call(
                tool_call={
                    "name": "fleet__schedule_task",
                    "arguments": {"agent_id": "agent-x", "when": "in 30 minutes", "instruction": "Follow up"},
                },
                workspace_id="ws-1",
                thread_id="thread-1",
                index=1,
                session_ctx={"authority_tier": "audience", "mandate_audience_tools": ["fleet.schedule_task"]},
                callbacks=self._callbacks(),
            )
        result = json.loads(raw)
        self.assertTrue(result["ok"])
        self.assertEqual(propose_mock.call_args.kwargs["payload"]["authority_tier"], "audience")

    def test_schedule_task_audience_tier_allowed_when_owner_lists_it_by_enforcement_id(self) -> None:
        """The Tools tab's Customer access control writes the tool's literal
        canonical enforcement id ("fleet__schedule_task"), not the connector.
        action dot form the tool above uses — mandate.audience_tools must
        recognize both id spaces, since fleet_get_agent_tools/the PATCH
        round-trip on the enforcement id exclusively."""
        with patch(
            "server_modules.bounded_scheduler_service.propose_self_wakeup",
            new=AsyncMock(return_value={"accepted": True, "wake_request": {"id": "wake-3"}}),
        ) as propose_mock:
            raw = skills_service.execute_single_direct_tool_call(
                tool_call={
                    "name": "fleet__schedule_task",
                    "arguments": {"agent_id": "agent-x", "when": "in 30 minutes", "instruction": "Follow up"},
                },
                workspace_id="ws-1",
                thread_id="thread-1",
                index=1,
                session_ctx={"authority_tier": "audience", "mandate_audience_tools": ["fleet__schedule_task"]},
                callbacks=self._callbacks(),
            )
        result = json.loads(raw)
        self.assertTrue(result["ok"])
        self.assertEqual(propose_mock.call_args.kwargs["payload"]["authority_tier"], "audience")

    def test_schedule_task_with_no_agent_id_schedules_the_caller_itself(self) -> None:
        """MAN-68 doctrine fix (audit-system-prompt-doctrine.md #1 ranked
        gap): the model was never told a self-wakeup scheduler exists, and
        this dispatcher used to hard-require agent_id even though
        bounded_scheduler_service.propose_self_wakeup (what schedule_task
        actually calls) takes no agent_id parameter at all — it always
        resolves the wake-up to the calling workspace's own master install.
        Omitting agent_id must succeed (not raise "requires agent_id") and
        fall back to the caller's own actor identity — this is the mechanism
        the standing-order ("check on X every morning") scenario needs: the
        owner-facing agent scheduling ITSELF, with no agent_id to supply."""
        with patch(
            "server_modules.bounded_scheduler_service.propose_self_wakeup",
            new=AsyncMock(return_value={"accepted": True, "wake_request": {"id": "wake-self-1"}}),
        ) as propose_mock:
            raw = skills_service.execute_single_direct_tool_call(
                tool_call={
                    "name": "fleet__schedule_task",
                    "arguments": {"when": "in 24 hours", "instruction": "Check inbox and summarize."},
                },
                workspace_id="ws-1",
                thread_id="thread-1",
                index=1,
                session_ctx={"authority_tier": "owner"},
                callbacks=self._callbacks(),
            )
        result = json.loads(raw)
        self.assertTrue(result["ok"])
        self.assertTrue(propose_mock.called)
        # No agent_id supplied by the model, yet the wake-up was still
        # proposed successfully — self-scheduling, not a validation error.
        self.assertEqual(propose_mock.call_args.kwargs["payload"]["authority_tier"], "owner")


class SubagentSpawnDispatchTests(unittest.TestCase):
    """execute_single_direct_tool_call's connector_id=="subagent" branch
    (2026-07-24 ruling) -- the live dispatch seam between the chat tool call
    and runtime_run_delegation_service.spawn_subagent_from_chat_turn."""

    def _callbacks(self) -> direct_tool_execution_service.DirectToolExecutionCallbacks:
        return direct_tool_execution_service.DirectToolExecutionCallbacks(
            compact_step_detail=lambda value: None,
            titleize_direct_step_token=lambda value: str(value or ""),
            run_async_tool_call=lambda awaitable: asyncio.run(awaitable),
            parse_tool_name=direct_chat_operator_binding_service.parse_tool_name,
            tool_arguments_payload=lambda payload: payload if isinstance(payload, dict) else {},
            parse_json_object_loose=lambda value: {},
            safe_positive_int=lambda value, default=0: int(value) if str(value or "").strip().isdigit() else default,
            normalize_reasoning_effort=lambda value: str(value or "").strip().lower() or None,
            build_direct_local_tool_config=skills_service.build_direct_local_tool_config,
            format_direct_local_tool_result=lambda result: json.dumps(result, ensure_ascii=False),
            build_direct_tool_config=lambda connector_id, action_id, tool_input: {
                "connector": connector_id, "action": action_id, "input": tool_input,
            },
            format_direct_tool_result=lambda result: json.dumps(result, ensure_ascii=False),
            llm_task=lambda *args, **kwargs: {"ok": True},
            web_search=lambda query: [],
            web_fetch=lambda url: f"Fetched {url}",
            search_memory_notebook=lambda workspace_id, query, max_results=5, agent_install_id=None: [],
            get_memory_notebook_excerpt=lambda workspace_id, rel_path, from_line=None, line_count=None, agent_install_id=None: {},
        )

    def test_disabled_specialist_guard_refuses_without_calling_the_bridge(self) -> None:
        """Defense in depth: even if a stale/cached tool list somehow let the
        model call subagent__spawn, the dispatch branch re-checks
        session_ctx["specialist_guard"]["subagents_enabled"] itself and must
        refuse WITHOUT ever calling into the real spawn bridge."""
        with patch(
            "server_modules.runtime_run_delegation_service.spawn_subagent_from_chat_turn"
        ) as bridge_mock:
            raw = skills_service.execute_single_direct_tool_call(
                tool_call={"name": "subagent__spawn", "arguments": {"task_description": "Do a thing"}},
                workspace_id="ws-1",
                thread_id="thread-1",
                session_ctx={
                    "authority_tier": "owner",
                    "specialist_guard": {"agent_install_id": "agent-pixel", "subagents_enabled": False},
                },
                callbacks=self._callbacks(),
            )
        result = json.loads(raw)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "subagents_disabled")
        bridge_mock.assert_not_called()

    def test_missing_specialist_guard_refuses_without_calling_the_bridge(self) -> None:
        """No specialist_guard at all (e.g. master/Sage's own turn, or a
        session_ctx built before this feature existed) must fail CLOSED, not
        open -- same "deny-more, never allow-more" convention as the rest of
        the specialist toolset machinery."""
        with patch(
            "server_modules.runtime_run_delegation_service.spawn_subagent_from_chat_turn"
        ) as bridge_mock:
            raw = skills_service.execute_single_direct_tool_call(
                tool_call={"name": "subagent__spawn", "arguments": {"task_description": "Do a thing"}},
                workspace_id="ws-1",
                thread_id="thread-1",
                session_ctx={"authority_tier": "owner"},
                callbacks=self._callbacks(),
            )
        result = json.loads(raw)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "subagents_disabled")
        bridge_mock.assert_not_called()

    def test_enabled_specialist_guard_reaches_the_real_bridge_with_resolved_identity(self) -> None:
        with patch(
            "server_modules.runtime_run_delegation_service.spawn_subagent_from_chat_turn",
            return_value={"ok": True, "run_id": "child-1", "status": "completed", "summary": "Done.",
                           "spawns_used": 1, "spawns_remaining": 4},
        ) as bridge_mock:
            raw = skills_service.execute_single_direct_tool_call(
                tool_call={
                    "name": "subagent__spawn",
                    "arguments": {"task_description": "Summarize the tickets.", "role": "support"},
                },
                workspace_id="ws-1",
                thread_id="thread-1",
                session_ctx={
                    "authority_tier": "owner",
                    "tenant_id": "tenant-1",
                    "active_agent_install_id": "agent-pixel",
                    "specialist_guard": {"agent_install_id": "agent-pixel", "subagents_enabled": True},
                },
                callbacks=self._callbacks(),
            )
        result = json.loads(raw)
        self.assertTrue(result["ok"])
        self.assertEqual(result["run_id"], "child-1")
        bridge_mock.assert_called_once()
        call_kwargs = bridge_mock.call_args.kwargs
        self.assertEqual(call_kwargs["task_description"], "Summarize the tickets.")
        self.assertEqual(call_kwargs["role"], "support")
        self.assertEqual(call_kwargs["workspace_id"], "ws-1")
        self.assertEqual(call_kwargs["tenant_id"], "tenant-1")
        self.assertEqual(call_kwargs["acting_agent_install_id"], "agent-pixel")

    def test_audience_tier_is_blocked_before_reaching_the_bridge(self) -> None:
        """subagent__spawn has no ToolDescriptor (audience_safe defaults
        False) and is never added to mandate_audience_tools by default --
        an audience-tier caller must be blocked by the mandate gate itself,
        never reaching the specialist_guard check or the bridge."""
        with patch(
            "server_modules.runtime_run_delegation_service.spawn_subagent_from_chat_turn"
        ) as bridge_mock:
            with self.assertRaises(RuntimeError) as ctx:
                skills_service.execute_single_direct_tool_call(
                    tool_call={"name": "subagent__spawn", "arguments": {"task_description": "Do a thing"}},
                    workspace_id="ws-1",
                    thread_id="thread-1",
                    session_ctx={
                        "authority_tier": "audience",
                        "specialist_guard": {"agent_install_id": "agent-pixel", "subagents_enabled": True},
                    },
                    callbacks=self._callbacks(),
                )
        self.assertEqual(str(ctx.exception), authority_mandate_service.MANDATE_BLOCKED_MESSAGE)
        bridge_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()

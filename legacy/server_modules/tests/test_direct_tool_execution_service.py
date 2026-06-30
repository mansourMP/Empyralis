import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from server_modules import direct_tool_execution_service as service
from server_modules import tool_broker_guard_service


def _callbacks() -> service.DirectToolExecutionCallbacks:
    return service.DirectToolExecutionCallbacks(
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
        build_direct_local_tool_config=lambda connector_id, action_id, arguments: ("read", {"connector": connector_id, "action": action_id}),
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


class DirectToolExecutionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        tool_broker_guard_service.reset_state_for_tests()

    def test_direct_tool_step_payload_formats_computer_click(self) -> None:
        payload = service.direct_tool_step_payload(
            "computer",
            "click",
            {"text": "Submit button"},
            step_id="step-1",
            status="running",
            callbacks=_callbacks(),
        )

        self.assertEqual(payload["kind"], "computer")
        self.assertEqual(payload["label"], "Clicking screen")
        self.assertEqual(payload["detail"], "Submit button")

    def test_extract_first_url_and_path_reference_handle_common_inputs(self) -> None:
        self.assertEqual(service.extract_first_url("Check example.com/docs please"), "https://example.com/docs")
        self.assertEqual(service.extract_first_path_reference("Open ./docs/README.md now"), "./docs/README.md")

    def test_hardware_screenshot_trace_metadata_exposes_artifact(self) -> None:
        metadata = service.build_direct_tool_trace_metadata(
            "hardware",
            "action",
            {
                "runtime_target": "user_device_gateway",
                "action": "screenshot.capture",
                "arguments": {},
            },
            result_text=json.dumps(
                {
                    "status": "completed",
                    "artifacts": ["artifact-screenshot-1"],
                    "runtime_session": {"gateway_id": "gw-1"},
                }
            ),
        )

        self.assertEqual(metadata["capability_id"], "screenshot.capture")
        self.assertEqual(metadata["result_summary"], "Captured screenshot from Agent Computer.")
        self.assertEqual(metadata["browser_screenshot"]["artifact_id"], "artifact-screenshot-1")
        self.assertEqual(metadata["browser_screenshot"]["caption"], "Agent Computer screenshot")

    def test_hardware_offline_trace_metadata_uses_actionable_summary(self) -> None:
        metadata = service.build_direct_tool_trace_metadata(
            "hardware",
            "action",
            {
                "runtime_target": "user_device_gateway",
                "action": "screenshot.capture",
                "arguments": {},
            },
            result_text=json.dumps({"status": "offline", "reason": "gateway_offline"}),
        )

        self.assertIsNone(metadata["browser_screenshot"])
        self.assertEqual(metadata["result_summary"], "Agent Computer is not connected. Open Hardware to connect this Mac.")

    def test_execute_single_direct_tool_call_handles_memory_tools(self) -> None:
        callbacks = _callbacks()

        search_raw = service.execute_single_direct_tool_call(
            tool_call={"name": "memory_search", "arguments": {"query": "timezone", "max_results": 3}},
            workspace_id="default",
            thread_id="thread-1",
            callbacks=callbacks,
        )
        get_raw = service.execute_single_direct_tool_call(
            tool_call={"name": "memory_get", "arguments": {"path": "MEMORY.md", "from": 2, "lines": 4}},
            workspace_id="default",
            thread_id="thread-1",
            callbacks=callbacks,
        )

        self.assertEqual(json.loads(search_raw)["results"][0]["query"], "timezone")
        self.assertEqual(json.loads(search_raw)["results"][0]["max_results"], 3)
        self.assertEqual(json.loads(get_raw)["path"], "MEMORY.md")
        self.assertEqual(json.loads(get_raw)["from_line"], 2)

    def test_execute_single_direct_tool_call_handles_memory_update(self) -> None:
        raw = service.execute_single_direct_tool_call(
            tool_call={"name": "memory_update", "arguments": {"filename": "IDENTITY.md", "content": "# Identity\n\n- Role: founder\n"}},
            workspace_id="workspace-1",
            thread_id="thread-1",
            callbacks=_callbacks(),
        )

        payload = json.loads(raw)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["filename"], "IDENTITY.md")
        self.assertEqual(payload["workspace_id"], "workspace-1")

    def test_execute_single_direct_tool_call_handles_memory_append_daily_note(self) -> None:
        raw = service.execute_single_direct_tool_call(
            tool_call={
                "name": "memory_append_daily_note",
                "arguments": {"note": "Preference: keep memory updates concise and structured."},
            },
            workspace_id="workspace-1",
            thread_id="thread-1",
            callbacks=_callbacks(),
        )

        payload = json.loads(raw)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["filename"], "memory/2026-05-11.md")
        self.assertEqual(payload["workspace_id"], "workspace-1")
        self.assertTrue(payload["saved"])
        self.assertIn("Preference:", payload["appended_entry"])

    def test_execute_single_direct_tool_call_handles_memory_stage_consolidation(self) -> None:
        raw = service.execute_single_direct_tool_call(
            tool_call={
                "name": "memory_stage_consolidation",
                "arguments": {
                    "proposal": "Decision: consolidate stable goals into GOALS.md and procedures into PROCEDURES.md.",
                    "target_files": ["GOALS.md", "PROCEDURES.md"],
                    "source_refs": ["memory/2026-05-11.md#L4"],
                },
            },
            workspace_id="workspace-1",
            thread_id="thread-1",
            callbacks=_callbacks(),
        )
        payload = json.loads(raw)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["staged_only"])
        self.assertEqual(payload["workspace_id"], "workspace-1")
        self.assertEqual(payload["target_files"], ["GOALS.md", "PROCEDURES.md"])

    def test_execute_single_direct_tool_call_handles_memory_consolidate_daily_notes(self) -> None:
        raw = service.execute_single_direct_tool_call(
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
            workspace_id="workspace-1",
            thread_id="thread-1",
            callbacks=_callbacks(),
        )
        payload = json.loads(raw)
        self.assertEqual(payload["workspace_id"], "workspace-1")
        self.assertTrue(payload["merged"])
        self.assertEqual(payload["audit_id"], "audit-1")

    def test_execute_single_direct_tool_call_handles_memory_list_versions(self) -> None:
        raw = service.execute_single_direct_tool_call(
            tool_call={"name": "memory_list_versions", "arguments": {"filename": "MEMORY.md", "limit": 5}},
            workspace_id="workspace-1",
            thread_id="thread-1",
            callbacks=_callbacks(),
        )
        payload = json.loads(raw)
        self.assertEqual(payload["filename"], "MEMORY.md")
        self.assertEqual(payload["versions"][0]["version_id"], "ver-1")

    def test_execute_single_direct_tool_call_handles_memory_rollback_version(self) -> None:
        raw = service.execute_single_direct_tool_call(
            tool_call={
                "name": "memory_rollback_version",
                "arguments": {"filename": "MEMORY.md", "version_id": "ver-1", "reason": "requested"},
            },
            workspace_id="workspace-1",
            thread_id="thread-1",
            callbacks=_callbacks(),
        )
        payload = json.loads(raw)
        self.assertEqual(payload["filename"], "MEMORY.md")
        self.assertEqual(payload["rolled_back_to_version_id"], "ver-1")

    def test_execute_single_direct_tool_call_emits_audit_events(self) -> None:
        callbacks = _callbacks()

        with patch(
            "server_modules.direct_tool_execution_service.security_audit_service.emit_security_audit_event"
        ) as emit_audit, patch(
            "server_modules.direct_tool_execution_service.agent_action_metering_service.record_started_sync"
        ) as record_started, patch(
            "server_modules.direct_tool_execution_service.agent_action_metering_service.record_completed_sync"
        ) as record_completed:
            raw = service.execute_single_direct_tool_call(
                tool_call={"name": "memory_search", "arguments": {"query": "timezone", "max_results": 3}},
                workspace_id="workspace-1",
                thread_id="thread-1",
                callbacks=callbacks,
                session_ctx={"tenant_id": "tenant-1", "request_id": "req-1"},
                provider="deepseek",
                model="deepseek-chat",
            )

        self.assertEqual(json.loads(raw)["results"][0]["query"], "timezone")
        self.assertEqual([call.kwargs["action"] for call in emit_audit.call_args_list], [
            "direct_tool.started",
            "direct_tool.completed",
        ])
        started = emit_audit.call_args_list[0].kwargs
        self.assertEqual(started["tenant_id"], "tenant-1")
        self.assertEqual(started["workspace_id"], "workspace-1")
        self.assertEqual(started["run_id"], "req-1")
        record_started.assert_called_once()
        record_completed.assert_called_once()
        self.assertEqual(record_completed.call_args.kwargs["action_domain"], "tool")
        self.assertEqual(record_completed.call_args.kwargs["source_table"], "direct_tool_calls")
        self.assertEqual(started["metadata"]["connector_id"], "memory")
        self.assertEqual(started["metadata"]["action_id"], "search")
        self.assertEqual(started["metadata"]["provider"], "deepseek")

    def test_execute_single_direct_tool_call_redacts_audit_argument_summary(self) -> None:
        callbacks = _callbacks()
        callbacks = service.DirectToolExecutionCallbacks(
            **{
                **vars(callbacks),
                "build_direct_local_tool_config": lambda connector_id, action_id, arguments: (
                    "read",
                    {"connector": connector_id, "action": action_id},
                ),
            }
        )

        with patch(
            "server_modules.direct_tool_execution_service.security_audit_service.emit_security_audit_event"
        ) as emit_audit, patch(
            "server_modules.skills_service._execute_safe_direct_local_tool_call",
            return_value="ok",
        ):
            service.execute_single_direct_tool_call(
                tool_call={
                    "name": "shell_exec",
                    "arguments": {"command": "curl -H 'Authorization: Bearer sk-secret123456' https://example.com"},
                },
                workspace_id="workspace-1",
                thread_id="thread-1",
                callbacks=callbacks,
            )

        summary = emit_audit.call_args_list[0].kwargs["metadata"]["argument_summary"]
        self.assertIn("[redacted-secret]", summary)
        self.assertNotIn("sk-secret123456", summary)

    def test_execute_single_direct_tool_call_redacts_audit_result_summary(self) -> None:
        callbacks = _callbacks()
        callbacks = service.DirectToolExecutionCallbacks(
            **{
                **vars(callbacks),
                "build_direct_local_tool_config": lambda connector_id, action_id, arguments: (
                    "read",
                    {"connector": connector_id, "action": action_id},
                ),
            }
        )

        with patch(
            "server_modules.direct_tool_execution_service.security_audit_service.emit_security_audit_event"
        ) as emit_audit, patch(
            "server_modules.skills_service._execute_safe_direct_local_tool_call",
            return_value="token=sk-secret123456",
        ):
            service.execute_single_direct_tool_call(
                tool_call={
                    "name": "shell_exec",
                    "arguments": {"command": "echo ok"},
                },
                workspace_id="workspace-1",
                thread_id="thread-1",
                callbacks=callbacks,
            )

        completed = emit_audit.call_args_list[1].kwargs["metadata"]["result_summary"]
        self.assertIn("[redacted-secret]", completed)
        self.assertNotIn("sk-secret123456", completed)

    def test_execute_single_direct_tool_call_redacts_unknown_high_entropy_result_summary(self) -> None:
        callbacks = _callbacks()
        callbacks = service.DirectToolExecutionCallbacks(
            **{
                **vars(callbacks),
                "build_direct_local_tool_config": lambda connector_id, action_id, arguments: (
                    "read",
                    {"connector": connector_id, "action": action_id},
                ),
            }
        )
        token = "Ab9_Qx7Lm5Np3Rs8Yv2Kt6Wd4Fg1Hj"

        with patch(
            "server_modules.direct_tool_execution_service.security_audit_service.emit_security_audit_event"
        ) as emit_audit, patch(
            "server_modules.skills_service._execute_safe_direct_local_tool_call",
            return_value=f"connector returned {token}",
        ):
            service.execute_single_direct_tool_call(
                tool_call={
                    "name": "shell_exec",
                    "arguments": {"command": "echo ok"},
                },
                workspace_id="workspace-1",
                thread_id="thread-1",
                callbacks=callbacks,
            )

        completed = emit_audit.call_args_list[1].kwargs["metadata"]["result_summary"]
        self.assertIn("[redacted-secret]", completed)
        self.assertNotIn(token, completed)

    def test_execute_single_direct_tool_call_emits_governance_metadata_for_shell(self) -> None:
        callbacks = _callbacks()
        callbacks = service.DirectToolExecutionCallbacks(
            **{
                **vars(callbacks),
                "build_direct_local_tool_config": lambda connector_id, action_id, arguments: (
                    "read",
                    {"connector": connector_id, "action": action_id},
                ),
            }
        )

        with patch(
            "server_modules.direct_tool_execution_service.security_audit_service.emit_security_audit_event"
        ) as emit_audit, patch(
            "server_modules.skills_service._execute_safe_direct_local_tool_call",
            return_value="ok",
        ):
            service.execute_single_direct_tool_call(
                tool_call={"name": "shell_exec", "arguments": {"command": "ls -la ./docs"}},
                workspace_id="workspace-1",
                thread_id="thread-1",
                callbacks=callbacks,
            )

        metadata = emit_audit.call_args_list[0].kwargs["metadata"]
        self.assertEqual(metadata["action_class"], "shell_execute")
        self.assertEqual(metadata["risk_level"], "high")
        self.assertEqual(metadata["governance_boundary"], "local_shell")
        self.assertTrue(metadata["requires_approval"])
        self.assertEqual(metadata["approval_reason"], "Shell execution")
        self.assertEqual(metadata["argument_target"], "ls -la ./docs")

    def test_execute_single_direct_tool_call_emits_governance_metadata_for_browser_mutation(self) -> None:
        callbacks = _callbacks()

        class _FakeBrowser:
            __empyralis_browser_adapter__ = True

            def run_sync(self, action, *args):
                return {"action": action, "args": list(args)}

        with patch(
            "server_modules.direct_tool_execution_service.security_audit_service.emit_security_audit_event"
        ) as emit_audit, patch(
            "server_modules.skills_service._resolve_direct_tool_browser_adapter",
            return_value=_FakeBrowser(),
        ):
            service.execute_single_direct_tool_call(
                tool_call={"name": "browser__fill", "arguments": {"selector": "#email", "value": "test@example.com"}},
                workspace_id="workspace-1",
                thread_id="thread-1",
                callbacks=callbacks,
            )

        metadata = emit_audit.call_args_list[0].kwargs["metadata"]
        self.assertEqual(metadata["action_class"], "browser_mutation")
        self.assertEqual(metadata["risk_level"], "high")
        self.assertEqual(metadata["governance_boundary"], "browser_session")
        self.assertTrue(metadata["requires_approval"])
        self.assertEqual(metadata["approval_reason"], "Browser mutation")
        self.assertEqual(metadata["argument_target"], "#email")

    def test_execute_single_direct_tool_call_routes_cloud_computer_actions_through_runtime(self) -> None:
        callbacks = service.DirectToolExecutionCallbacks(
            **{
                **vars(_callbacks()),
                "run_async_tool_call": lambda awaitable: asyncio.run(awaitable),
            }
        )

        with patch(
            "server_modules.direct_tool_execution_service.security_audit_service.emit_security_audit_event"
        ), patch(
            "server_modules.direct_tool_execution_service.deployed_agent_virtual_runtime_service.execute_bound_cloud_runtime_tool_call",
            new=AsyncMock(return_value='{"status":"ok","action_result":{"ok":true}}'),
        ) as runtime_exec, patch(
            "server_modules.skills_service._resolve_direct_tool_browser_adapter",
        ) as browser_adapter:
            result = service.execute_single_direct_tool_call(
                tool_call={"name": "browser__navigate", "arguments": {"url": "https://supplier.example"}},
                workspace_id="workspace-1",
                thread_id="thread-1",
                callbacks=callbacks,
                session_ctx={
                    "tenant_id": "tenant-1",
                    "agent_turn_request": {
                        "context_hints": {
                            "metadata": {
                                "deployed_agent_id": "dagent_1",
                                "runtime_session_id": "vcsess_1",
                                "runtime_session_binding": "cloud_computer_agent",
                            }
                        }
                    },
                },
            )

        self.assertIn('"status":"ok"', result)
        runtime_exec.assert_awaited_once()
        browser_adapter.assert_not_called()

    def test_execute_single_direct_tool_call_cloud_computer_unsupported_action_fails_closed(self) -> None:
        callbacks = service.DirectToolExecutionCallbacks(
            **{
                **vars(_callbacks()),
                "run_async_tool_call": lambda awaitable: asyncio.run(awaitable),
            }
        )

        with patch(
            "server_modules.direct_tool_execution_service.security_audit_service.emit_security_audit_event"
        ), patch(
            "server_modules.direct_tool_execution_service.deployed_agent_virtual_runtime_service.execute_bound_cloud_runtime_tool_call",
            new=AsyncMock(side_effect=RuntimeError("Cloud Computer runtime does not support browser__fill.")),
        ), patch(
            "server_modules.skills_service._resolve_direct_tool_browser_adapter",
        ) as browser_adapter:
            with self.assertRaisesRegex(RuntimeError, "browser__fill"):
                service.execute_single_direct_tool_call(
                    tool_call={"name": "browser__fill", "arguments": {"selector": "#email", "value": "x"}},
                    workspace_id="workspace-1",
                    thread_id="thread-1",
                    callbacks=callbacks,
                    session_ctx={
                        "tenant_id": "tenant-1",
                        "agent_turn_request": {
                            "context_hints": {
                                "metadata": {
                                    "deployed_agent_id": "dagent_1",
                                    "runtime_session_id": "vcsess_1",
                                    "runtime_session_binding": "cloud_computer_agent",
                                }
                            }
                        },
                    },
                )

        browser_adapter.assert_not_called()

    def test_execute_single_direct_tool_call_routes_self_hosted_actions_through_runtime(self) -> None:
        callbacks = service.DirectToolExecutionCallbacks(
            **{
                **vars(_callbacks()),
                "run_async_tool_call": lambda awaitable: asyncio.run(awaitable),
            }
        )

        with patch(
            "server_modules.direct_tool_execution_service.security_audit_service.emit_security_audit_event"
        ), patch(
            "server_modules.direct_tool_execution_service.deployed_agent_virtual_runtime_service.execute_bound_self_hosted_runtime_tool_call",
            new=AsyncMock(return_value='{"status":"ok","runtime":"self_hosted"}'),
        ) as self_hosted_exec, patch(
            "server_modules.direct_tool_execution_service.deployed_agent_virtual_runtime_service.execute_bound_cloud_runtime_tool_call",
            new=AsyncMock(return_value=None),
        ) as cloud_exec, patch(
            "server_modules.skills_service._resolve_direct_tool_browser_adapter",
        ) as browser_adapter:
            result = service.execute_single_direct_tool_call(
                tool_call={"name": "browser__navigate", "arguments": {"url": "https://intranet.example"}},
                workspace_id="workspace-1",
                thread_id="thread-1",
                callbacks=callbacks,
                session_ctx={
                    "tenant_id": "tenant-1",
                    "agent_turn_request": {
                        "context_hints": {
                            "metadata": {
                                "deployed_agent_id": "dagent_1",
                                "runtime_session_id": "shsess_1",
                                "runtime_session_binding": "self_hosted_agent",
                            }
                        }
                    },
                },
            )

        self.assertIn('"runtime":"self_hosted"', result)
        self_hosted_exec.assert_awaited_once()
        cloud_exec.assert_not_awaited()
        browser_adapter.assert_not_called()

    def test_execute_single_direct_tool_call_self_hosted_binding_never_falls_back_to_generic(self) -> None:
        callbacks = service.DirectToolExecutionCallbacks(
            **{
                **vars(_callbacks()),
                "run_async_tool_call": lambda awaitable: asyncio.run(awaitable),
            }
        )

        with patch(
            "server_modules.direct_tool_execution_service.security_audit_service.emit_security_audit_event"
        ), patch(
            "server_modules.direct_tool_execution_service.deployed_agent_virtual_runtime_service.execute_bound_self_hosted_runtime_tool_call",
            new=AsyncMock(return_value=None),
        ), patch(
            "server_modules.skills_service._resolve_direct_tool_browser_adapter",
        ) as browser_adapter:
            with self.assertRaisesRegex(RuntimeError, "must execute through bound node runtime"):
                service.execute_single_direct_tool_call(
                    tool_call={"name": "browser__navigate", "arguments": {"url": "https://intranet.example"}},
                    workspace_id="workspace-1",
                    thread_id="thread-1",
                    callbacks=callbacks,
                    session_ctx={
                        "tenant_id": "tenant-1",
                        "agent_turn_request": {
                            "context_hints": {
                                "metadata": {
                                    "deployed_agent_id": "dagent_1",
                                    "runtime_session_id": "shsess_1",
                                    "runtime_session_binding": "self_hosted_agent",
                                }
                            }
                        },
                    },
                )

        browser_adapter.assert_not_called()

    def test_execute_single_direct_tool_call_uses_broker_guard_for_execute_actions(self) -> None:
        callbacks = _callbacks()
        callbacks = service.DirectToolExecutionCallbacks(
            **{
                **vars(callbacks),
                "build_direct_local_tool_config": lambda connector_id, action_id, arguments: (
                    "read",
                    {"connector": connector_id, "action": action_id},
                ),
            }
        )

        with patch.dict(os.environ, {"EMPYRALIS_TOOL_BROKER_EXECUTE_LIMIT": "1"}, clear=False), patch(
            "server_modules.direct_tool_execution_service.security_audit_service.emit_security_audit_event"
        ) as emit_audit, patch(
            "server_modules.direct_tool_execution_service.agent_action_metering_service.record_blocked_sync"
        ) as record_blocked, patch(
            "server_modules.skills_service._execute_safe_direct_local_tool_call",
            return_value="ok",
        ):
            first = service.execute_single_direct_tool_call(
                tool_call={"name": "shell_exec", "arguments": {"command": "echo one"}},
                workspace_id="workspace-1",
                thread_id="thread-1",
                callbacks=callbacks,
                session_ctx={"tenant_id": "tenant-1", "request_id": "req-guard"},
            )
            self.assertEqual(first, "ok")
            with self.assertRaises(RuntimeError):
                service.execute_single_direct_tool_call(
                    tool_call={"name": "shell_exec", "arguments": {"command": "echo two"}},
                    workspace_id="workspace-1",
                    thread_id="thread-1",
                    callbacks=callbacks,
                    session_ctx={"tenant_id": "tenant-1", "request_id": "req-guard"},
                )

        self.assertIn("direct_tool.blocked", [call.kwargs["action"] for call in emit_audit.call_args_list])
        record_blocked.assert_called_once()
        self.assertEqual(record_blocked.call_args.kwargs["policy_decision"], "blocked")

    def test_execute_single_direct_tool_call_emits_failed_audit_event(self) -> None:
        callbacks = _callbacks()

        with patch(
            "server_modules.direct_tool_execution_service.security_audit_service.emit_security_audit_event"
        ) as emit_audit, patch(
            "server_modules.direct_tool_execution_service.agent_action_metering_service.record_failed_sync"
        ) as record_failed:
            with self.assertRaises(RuntimeError):
                service.execute_single_direct_tool_call(
                    tool_call={"name": "http_request", "arguments": {"url": "https://example.com"}},
                    workspace_id="workspace-1",
                    thread_id="thread-1",
                    callbacks=callbacks,
                    session_ctx={"tenant_id": "tenant-1", "request_id": "req-1"},
                )

        self.assertEqual([call.kwargs["action"] for call in emit_audit.call_args_list], [
            "direct_tool.started",
            "direct_tool.failed",
        ])
        self.assertEqual(emit_audit.call_args_list[1].kwargs["metadata"]["error_type"], "RuntimeError")
        record_failed.assert_called_once()
        self.assertEqual(record_failed.call_args.kwargs["error_code"], "RuntimeError")

    def test_execute_single_direct_tool_call_handles_sage_service_tools(self) -> None:
        callbacks = _callbacks()
        callbacks = service.DirectToolExecutionCallbacks(
            **{
                **vars(callbacks),
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
                created_raw = service.execute_single_direct_tool_call(
                    tool_call={
                        "name": "sage_service__create_entry",
                        "arguments": {
                            "service_id": "nutrition_log",
                            "entry": {
                                "meal": "Lunch",
                                "calories": 650,
                                "protein_grams": 42,
                            },
                            "explicit_user_intent": True,
                        },
                    },
                    workspace_id="workspace-1",
                    thread_id="thread-1",
                    callbacks=callbacks,
                    session_ctx={"tenant_id": "tenant-1"},
                )
                listed_raw = service.execute_single_direct_tool_call(
                    tool_call={
                        "name": "sage_service__list_state",
                        "arguments": {"service_id": "nutrition_log"},
                    },
                    workspace_id="workspace-1",
                    thread_id="thread-1",
                    callbacks=callbacks,
                    session_ctx={"tenant_id": "tenant-1"},
                )

        created = json.loads(created_raw)
        listed = json.loads(listed_raw)
        self.assertEqual(created["entries"][0]["meal"], "Lunch")
        self.assertEqual(listed["entries"][0]["protein_grams"], 42)

    def test_execute_direct_tool_calls_aggregates_non_empty_results(self) -> None:
        raw = service.execute_direct_tool_calls(
            tool_calls=[{"name": "one"}, {"name": "two"}],
            workspace_id="default",
            thread_id="thread-1",
            execute_single_tool_call=lambda **kwargs: kwargs["tool_call"]["name"],
        )

        self.assertEqual(raw, "one\n\ntwo")


if __name__ == "__main__":
    unittest.main()

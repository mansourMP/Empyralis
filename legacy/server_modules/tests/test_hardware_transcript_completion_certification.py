from __future__ import annotations

from contextlib import asynccontextmanager
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from server_modules import hardware_action_broker_service as broker
from server_modules import thread_service
from server_modules.agent_trace_service import TraceContext


@asynccontextmanager
async def _local_control_plane_connection(**_kwargs):
    yield None


def _trace(trace_id: str, thread_id: str, run_id: str) -> TraceContext:
    return TraceContext(
        trace_id=trace_id,
        workspace_id="ws-cert",
        tenant_id="tenant-cert",
        thread_id=thread_id,
        run_id=run_id,
        root_agent_id="sage",
    )


class _FakeCloudRuntime:
    def __init__(self) -> None:
        self.create_session = AsyncMock(
            return_value={
                "session_id": "cloud-session-cert",
                "status": "started",
                "runtime_kind": "virtual_computer_runtime",
            }
        )
        self.execute_action = AsyncMock(
            return_value={
                "session_id": "cloud-session-cert",
                "status": "completed",
                "runtime_kind": "virtual_computer_runtime",
                "action_result": {"ok": True, "action": "open_url"},
            }
        )
        self.stream_screenshot = AsyncMock(
            return_value={
                "session_id": "cloud-session-cert",
                "status": "completed",
                "runtime_kind": "virtual_computer_runtime",
                "artifact": {"artifact_id": "artifact-cloud-cert", "type": "screenshot"},
                "streaming_ui": {"live_screenshot": {"artifact_id": "artifact-cloud-cert"}},
            }
        )


class _FakeCloudRuntimeRegistry:
    def __init__(self, runtime: _FakeCloudRuntime) -> None:
        self.runtime = runtime

    def resolve(self, *_args, **_kwargs):
        return self.runtime


class HardwareTranscriptCompletionCertificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        suffix = uuid.uuid4().hex[:8]
        self.thread_id = f"thread-cert-{suffix}"
        self.trace_id = f"trace-cert-{suffix}"
        self.run_id = f"run-cert-{suffix}"

    async def _seed_assistant_turn(self) -> None:
        await thread_service.ensure_master_thread(
            thread_id=self.thread_id,
            tenant_id="tenant-cert",
            workspace_id="ws-cert",
            owner_user_id="user-cert",
            channel="web",
            title="Certification thread",
        )
        await thread_service.record_assistant_turn(
            thread_id=self.thread_id,
            tenant_id="tenant-cert",
            workspace_id="ws-cert",
            session_id="session-cert",
            actor={"type": "assistant", "id": "sage", "display_name": "Sage"},
            reply="Hardware result is ready.",
            status="completed",
            run_id=self.run_id,
            metadata={
                "request_id": "chat-request-cert",
                "trace_id": self.trace_id,
                "run_id": self.run_id,
            },
        )

    async def _assistant_transcript_events(self) -> list[dict]:
        thread = await thread_service.get_thread(
            self.thread_id,
            tenant_id="tenant-cert",
            workspace_id="ws-cert",
            include_turns=True,
        )
        turns = list((thread or {}).get("turns") or [])
        assistant_turn = next(item for item in turns if item.get("role") == "assistant")
        metadata = assistant_turn.get("metadata") if isinstance(assistant_turn.get("metadata"), dict) else {}
        return list(metadata.get("transcript_events") or [])

    async def test_self_hosted_completion_patches_original_assistant_turn_for_replay(self) -> None:
        completion = {
            "ok": True,
            "status": "completed",
            "command_id": "shcmd-cert",
            "command": {
                "id": "shcmd-cert",
                "command_payload": {
                    "runtime_session_binding": broker.HARDWARE_RUNTIME_SESSION_BINDING,
                    "runtime_session_id": "hrs-self-cert",
                    "runtime_target": "self_hosted_node",
                    "tenant_id": "tenant-cert",
                    "workspace_id": "ws-cert",
                    "thread_id": self.thread_id,
                    "run_id": self.run_id,
                    "trace_id": self.trace_id,
                    "request_id": "hardware-request-cert",
                    "capability_id": "shell.execute",
                    "action_id": "shell.exec",
                    "arguments": {"command": "cat /secret/raw-command"},
                    "runtime_node_id": "node-cert",
                    "runtime_profile_id": "rprof-cert",
                },
            },
            "result_payload": {"summary": "Self-hosted command completed."},
            "artifacts": [{"artifact_id": "artifact-self-cert"}],
        }
        with (
            patch("server_modules.control_plane_repository._scoped_connection", new=_local_control_plane_connection),
            patch("server_modules.hardware_action_broker_service.session_service.get_session", new=AsyncMock(return_value={})),
            patch("server_modules.hardware_action_broker_service.session_service.extend_session", new=AsyncMock(return_value=None)),
            patch("server_modules.hardware_action_broker_service.agent_trace_service.resume_trace", new=AsyncMock(return_value=_trace(self.trace_id, self.thread_id, self.run_id))),
            patch("server_modules.hardware_action_broker_service.agent_trace_service.emit_tool_result", new=AsyncMock(return_value="evt-result")),
            patch("server_modules.hardware_action_broker_service.agent_trace_service.emit_artifact_created", new=AsyncMock(return_value="evt-artifact")),
        ):
            await self._seed_assistant_turn()
            await broker.record_self_hosted_command_completion(completion)
            events = await self._assistant_transcript_events()

        event_types = [event["payload"]["event_type"] for event in events]
        self.assertEqual(event_types, ["artifact.created", "tool.result"])
        self.assertTrue(all(event.get("schema_version") == 1 for event in events))
        self.assertEqual(events[0]["payload"]["artifact_id"], "artifact-self-cert")
        self.assertEqual(events[1]["payload"]["data"]["runtime_session_id"], "hrs-self-cert")
        self.assertNotIn("arguments", events[1]["payload"]["data"])
        self.assertNotIn("args_preview", events[1]["payload"]["data"])

    async def test_gateway_approval_completion_patches_original_assistant_turn_for_replay(self) -> None:
        result = {
            "status": "executed",
            "approval": {
                "approval_id": "gapproval-cert",
                "decision": "approved",
                "request_payload": {
                    "runtime_session_binding": broker.HARDWARE_RUNTIME_SESSION_BINDING,
                    "runtime_session_id": "hrs-gateway-cert",
                    "runtime_target": "user_device_gateway",
                    "tenant_id": "tenant-cert",
                    "workspace_id": "ws-cert",
                    "thread_id": self.thread_id,
                    "run_id": self.run_id,
                    "trace_id": self.trace_id,
                    "request_id": "gateway-request-cert",
                    "capability_id": "shell.execute",
                    "action_id": "shell.exec",
                    "arguments": {"command": "whoami"},
                },
            },
            "execution": {
                "request_id": "gateway-request-cert",
                "result": {
                    "summary": "Ran command.",
                    "artifacts": [{"id": "artifact-gateway-cert"}],
                },
            },
        }
        with (
            patch("server_modules.control_plane_repository._scoped_connection", new=_local_control_plane_connection),
            patch("server_modules.hardware_action_broker_service.session_service.get_session", new=AsyncMock(return_value={})),
            patch("server_modules.hardware_action_broker_service.session_service.extend_session", new=AsyncMock(return_value=None)),
            patch("server_modules.hardware_action_broker_service.agent_trace_service.resume_trace", new=AsyncMock(return_value=_trace(self.trace_id, self.thread_id, self.run_id))),
            patch("server_modules.hardware_action_broker_service.agent_trace_service.emit_approval_resolved", new=AsyncMock(return_value="evt-approval")),
            patch("server_modules.hardware_action_broker_service.agent_trace_service.emit_tool_result", new=AsyncMock(return_value="evt-result")),
            patch("server_modules.hardware_action_broker_service.agent_trace_service.emit_artifact_created", new=AsyncMock(return_value="evt-artifact")),
        ):
            await self._seed_assistant_turn()
            await broker.record_gateway_approval_resolution(result, actor="user-cert")
            events = await self._assistant_transcript_events()

        event_types = [event["payload"]["event_type"] for event in events]
        self.assertEqual(event_types, ["approval.resolved", "artifact.created", "tool.result"])
        self.assertEqual(events[0]["payload"]["approval_id"], "gapproval-cert")
        self.assertEqual(events[1]["payload"]["artifact_id"], "artifact-gateway-cert")
        self.assertEqual(events[2]["payload"]["data"]["runtime_target"], "user_device_gateway")

    async def test_cloud_computer_completion_patches_original_assistant_turn_for_replay(self) -> None:
        runtime = _FakeCloudRuntime()
        with (
            patch("server_modules.control_plane_repository._scoped_connection", new=_local_control_plane_connection),
            patch("server_modules.hardware_action_broker_service.session_service.create_session", new=AsyncMock(return_value="hrs-cloud-cert")),
            patch("server_modules.hardware_action_broker_service.session_service.extend_session", new=AsyncMock(return_value=None)),
            patch("server_modules.hardware_action_broker_service.agent_trace_service.resume_trace", new=AsyncMock(return_value=_trace(self.trace_id, self.thread_id, self.run_id))),
            patch("server_modules.hardware_action_broker_service.agent_trace_service.emit_tool_started", new=AsyncMock(return_value="evt-started")),
            patch("server_modules.hardware_action_broker_service.agent_trace_service.emit_tool_result", new=AsyncMock(return_value="evt-result")),
            patch("server_modules.hardware_action_broker_service.agent_trace_service.emit_artifact_created", new=AsyncMock(return_value="evt-artifact")),
            patch("server_modules.hardware_action_broker_service.get_cloud_computer_runtime_registry", return_value=_FakeCloudRuntimeRegistry(runtime)),
        ):
            await self._seed_assistant_turn()
            payload = await broker.execute_hardware_action(
                tenant_id="tenant-cert",
                workspace_id="ws-cert",
                user_id="user-cert",
                action_id="screenshot.capture",
                runtime_target="empyralis_cloud_computer",
                runtime_access_mode="full_access",
                run_id=self.run_id,
                trace_id=self.trace_id,
                thread_id=self.thread_id,
                request_id="cloud-request-cert",
                session_id="hrs-cloud-cert",
                trace_context=_trace(self.trace_id, self.thread_id, self.run_id),
            )
            events = await self._assistant_transcript_events()

        self.assertEqual(payload["status"], "completed")
        event_types = [event["payload"]["event_type"] for event in events]
        self.assertEqual(event_types, ["artifact.created", "tool.result"])
        self.assertEqual(events[0]["payload"]["artifact_id"], "artifact-cloud-cert")
        self.assertEqual(events[1]["payload"]["data"]["runtime_target"], "empyralis_cloud_computer")

    async def test_stop_cancel_patches_original_assistant_turn_for_replay(self) -> None:
        with (
            patch("server_modules.control_plane_repository._scoped_connection", new=_local_control_plane_connection),
            patch("server_modules.hardware_action_broker_service.session_service.extend_session", new=AsyncMock(return_value=None)),
            patch("server_modules.hardware_action_broker_service.gateway_state_repository.get_gateway_registration", return_value={
                "id": "gw-cert",
                "gateway_id": "gw-cert",
                "workspace_id": "ws-cert",
                "tenant_id": "tenant-cert",
                "device_trust_state": "trusted",
                "status": "active",
                "capabilities": ["shell.execute"],
            }),
            patch("server_modules.hardware_action_broker_service.gateway_protocol_service.gateway_connection_is_live", return_value=True),
            patch("server_modules.hardware_action_broker_service.gateway_execution_service.interrupt_tool_via_gateway", new=AsyncMock(return_value={
                "gateway_id": "gw-cert",
                "request_id": "stop-request-cert",
                "target_request_id": "chat-request-cert",
                "interrupted": True,
            })),
            patch("server_modules.hardware_action_broker_service.agent_trace_service.resume_trace", new=AsyncMock(return_value=_trace(self.trace_id, self.thread_id, self.run_id))),
            patch("server_modules.hardware_action_broker_service.agent_trace_service.emit_tool_result", new=AsyncMock(return_value="evt-stop")),
        ):
            await self._seed_assistant_turn()
            payload = await broker.stop_hardware_action(
                tenant_id="tenant-cert",
                workspace_id="ws-cert",
                runtime_target="user_device_gateway",
                gateway_id="gw-cert",
                run_id=self.run_id,
                trace_id=self.trace_id,
                request_id="chat-request-cert",
                thread_id=self.thread_id,
                target_request_id="chat-request-cert",
                reason="certification_stop",
                session_id="hrs-stop-cert",
            )
            events = await self._assistant_transcript_events()

        self.assertEqual(payload["status"], "terminated")
        self.assertEqual(payload["runtime_session"]["thread_id"], self.thread_id)
        self.assertEqual(payload["runtime_session"]["state"], "terminated")
        self.assertEqual([event["payload"]["event_type"] for event in events], ["tool.result"])
        self.assertEqual(events[0]["payload"]["data"]["status"], "terminated")
        self.assertEqual(events[0]["payload"]["data"]["runtime_target"], "user_device_gateway")
        self.assertEqual(events[0]["payload"]["data"]["runtime_session_id"], "hrs-stop-cert")
        self.assertNotIn("target_request_id", events[0]["payload"]["data"])
        self.assertNotIn("request_id", events[0]["payload"]["data"])


if __name__ == "__main__":
    unittest.main()

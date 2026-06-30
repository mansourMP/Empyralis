import json
import os

from server_modules import rust_runtime_kernel_client as client


def test_runtime_kernel_unavailable_fails_closed(monkeypatch):
    monkeypatch.setattr(client, "runtime_kernel_binary", lambda: None)

    response = client.run_runtime_kernel("validate-policy", {"policy": {"autonomy_mode": "yolo"}})

    assert response["ok"] is False
    assert response["decision"] == "block"
    assert response["reason"] == "runtime_kernel_unavailable"


def test_unknown_runtime_kernel_command_fails_closed_before_spawn(monkeypatch):
    def fail_if_called():
        raise AssertionError("runtime_kernel_binary should not be resolved for unknown commands")

    monkeypatch.setattr(client, "runtime_kernel_binary", fail_if_called)

    response = client.run_runtime_kernel("unknown-command", {})

    assert response["ok"] is False
    assert response["decision"] == "block"
    assert response["reason"] == "runtime_kernel_command_not_allowed"


def test_policy_preset_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "policy": {"autonomy_mode": "cautious"},
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.policy_preset("cautious")

    assert response["policy"]["autonomy_mode"] == "cautious"
    assert captured == {
        "command": "policy-preset",
        "payload": {"preset": "cautious"},
    }


def test_capability_manifest_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "capabilities": ["read_files"],
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.capability_manifest(include_aliases=False)

    assert response["capabilities"] == ["read_files"]
    assert captured == {
        "command": "capability-manifest",
        "payload": {"include_aliases": "false"},
    }


def test_control_plane_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "read_record",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.control_plane_decision(
        operation="read",
        record_type="workspace",
        workspace_id="w1",
        tenant_id="t1",
    )

    assert response["next_action"] == "read_record"
    assert captured == {
        "command": "control-plane-decision",
        "payload": {
            "operation": "read",
            "record_type": "workspace",
            "workspace_id": "w1",
            "tenant_id": "t1",
        },
    }


def test_sandbox_execution_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "build_hardened_container_command",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.sandbox_execution_decision(
        operation="build_docker_command",
        sandbox_root="/tmp/empyralis-sandbox-proof",
        network_mode="none",
    )

    assert response["next_action"] == "build_hardened_container_command"
    assert captured == {
        "command": "sandbox-execution-decision",
        "payload": {
            "operation": "build_docker_command",
            "sandbox_root": "/tmp/empyralis-sandbox-proof",
            "network_mode": "none",
        },
    }


def test_build_sandbox_command_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "build_hardened_container_command",
            "command": ["docker", "run"],
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.build_sandbox_command(
        sandbox_root="/tmp/empyralis-sandbox-proof",
        image="empyralis-sandbox:latest",
    )

    assert response["next_action"] == "build_hardened_container_command"
    assert response["command"] == ["docker", "run"]
    assert captured == {
        "command": "build-sandbox-command",
        "payload": {
            "sandbox_root": "/tmp/empyralis-sandbox-proof",
            "image": "empyralis-sandbox:latest",
        },
    }


def test_execution_runtime_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "dispatch_runtime_run",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.execution_runtime_decision(
        operation="dispatch_run",
        run_id="run1",
        workspace_id="w1",
        actor_id="u1",
        engine_id="tool_loop",
    )

    assert response["next_action"] == "dispatch_runtime_run"
    assert captured == {
        "command": "execution-runtime-decision",
        "payload": {
            "operation": "dispatch_run",
            "run_id": "run1",
            "workspace_id": "w1",
            "actor_id": "u1",
            "engine_id": "tool_loop",
        },
    }


def test_runtime_session_api_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "claim_runtime_task",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.runtime_session_api_decision(
        operation="runtime_task_claim",
        runtime_id="runtime1",
        session_token="token1",
        workspace_id="w1",
    )

    assert response["next_action"] == "claim_runtime_task"
    assert captured == {
        "command": "runtime-session-api-decision",
        "payload": {
            "operation": "runtime_task_claim",
            "runtime_id": "runtime1",
            "session_token": "token1",
            "workspace_id": "w1",
        },
    }


def test_runtime_state_store_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "write_runtime_session",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.runtime_state_store_decision(
        operation="upsert_runtime_session",
        session_id="session1",
        workspace_id="w1",
        status="active",
        payload={"session_id": "session1"},
    )

    assert response["next_action"] == "write_runtime_session"
    assert captured == {
        "command": "runtime-state-store-decision",
        "payload": {
            "operation": "upsert_runtime_session",
            "session_id": "session1",
            "workspace_id": "w1",
            "status": "active",
            "payload": {"session_id": "session1"},
        },
    }


def test_session_scheduler_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "return_session_status",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.session_scheduler_decision(
        operation="session_status",
        session_id="session1",
        turn_count=4,
    )

    assert response["next_action"] == "return_session_status"
    assert captured == {
        "command": "session-scheduler-decision",
        "payload": {
            "operation": "session_status",
            "session_id": "session1",
            "turn_count": 4,
        },
    }


def test_runtime_health_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "idle",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.runtime_health_decision(
        operation="heartbeat_snapshot",
        workspace_id="w1",
        rust_kernel={"available": True},
    )

    assert response["next_action"] == "idle"
    assert captured == {
        "command": "runtime-health-decision",
        "payload": {
            "operation": "heartbeat_snapshot",
            "workspace_id": "w1",
            "rust_kernel": {"available": True},
        },
    }


def test_platform_orchestration_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "create_run",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.platform_orchestration_decision(
        operation="run_create",
        workspace_id="w1",
        actor_id="u1",
    )

    assert response["next_action"] == "create_run"
    assert captured == {
        "command": "platform-orchestration-decision",
        "payload": {
            "operation": "run_create",
            "workspace_id": "w1",
            "actor_id": "u1",
        },
    }


def test_control_plane_service_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "apply_control_plane_write",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.control_plane_service_decision(
        operation="workspace_update",
        tenant_id="t1",
        workspace_id="w1",
        actor_id="u1",
        actor_role="admin",
    )

    assert response["next_action"] == "apply_control_plane_write"
    assert captured == {
        "command": "control-plane-service-decision",
        "payload": {
            "operation": "workspace_update",
            "tenant_id": "t1",
            "workspace_id": "w1",
            "actor_id": "u1",
            "actor_role": "admin",
        },
    }


def test_deployed_agent_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "route_live_agent",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.deployed_agent_decision(
        operation="public_route",
        workspace_id="w1",
        deployed_agent_id="agent1",
        deployment_state="live",
    )

    assert response["next_action"] == "route_live_agent"
    assert captured == {
        "command": "deployed-agent-decision",
        "payload": {
            "operation": "public_route",
            "workspace_id": "w1",
            "deployed_agent_id": "agent1",
            "deployment_state": "live",
        },
    }


def test_deployed_agent_service_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "deploy_deployed_agent",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.deployed_agent_service_decision(
        operation="deploy",
        workspace_id="w1",
        deployed_agent_id="agent1",
        actor_id="u1",
    )

    assert response["next_action"] == "deploy_deployed_agent"
    assert captured == {
        "command": "deployed-agent-service-decision",
        "payload": {
            "operation": "deploy",
            "workspace_id": "w1",
            "deployed_agent_id": "agent1",
            "actor_id": "u1",
        },
    }


def test_deployed_data_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "read_deployed_agent_conversation_detail",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.deployed_data_decision(
        operation="conversation_detail",
        workspace_id="w1",
        tenant_id="t1",
        deployed_agent_id="agent1",
        actor_role="admin",
        session_id="session1",
    )

    assert response["next_action"] == "read_deployed_agent_conversation_detail"
    assert captured == {
        "command": "deployed-data-decision",
        "payload": {
            "operation": "conversation_detail",
            "workspace_id": "w1",
            "tenant_id": "t1",
            "deployed_agent_id": "agent1",
            "actor_role": "admin",
            "session_id": "session1",
        },
    }


def test_deployed_readiness_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "deploy_deployed_agent",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.deployed_readiness_decision(
        stage="deploy",
        workspace_id="w1",
        tenant_id="t1",
        deployed_agent_id="agent1",
    )

    assert response["next_action"] == "deploy_deployed_agent"
    assert captured == {
        "command": "deployed-readiness-decision",
        "payload": {
            "stage": "deploy",
            "workspace_id": "w1",
            "tenant_id": "t1",
            "deployed_agent_id": "agent1",
        },
    }


def test_deployed_virtual_runtime_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "build_deployed_agent_virtual_runtime_payload",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.deployed_virtual_runtime_decision(
        operation="build_policy_payload",
        tenant_id="t1",
        workspace_id="w1",
        deployed_agent_id="agent1",
        studio_agent_mode="cloud_computer_agent",
        privacy_contract_valid=True,
        computer_safety_contract_valid=True,
        allowed_domain_count=1,
        daily_budget_usd_cents=100,
        session_recording_policy="screenshots",
    )

    assert response["next_action"] == "build_deployed_agent_virtual_runtime_payload"
    assert captured == {
        "command": "deployed-virtual-runtime-decision",
        "payload": {
            "operation": "build_policy_payload",
            "tenant_id": "t1",
            "workspace_id": "w1",
            "deployed_agent_id": "agent1",
            "studio_agent_mode": "cloud_computer_agent",
            "privacy_contract_valid": True,
            "computer_safety_contract_valid": True,
            "allowed_domain_count": 1,
            "daily_budget_usd_cents": 100,
            "session_recording_policy": "screenshots",
        },
    }


def test_deployed_virtual_runtime_service_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "create_bound_runtime_session",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.deployed_virtual_runtime_service_decision(
        operation="create_cloud_session",
        tenant_id="t1",
        workspace_id="w1",
        deployed_agent_id="agent1",
        studio_agent_mode="cloud_computer_agent",
        runtime_provider_id="browserbase",
        privacy_contract_valid=True,
        computer_safety_contract_valid=True,
        allowed_domain_count=1,
        daily_budget_usd_cents=100,
        session_timeout_seconds=300,
        max_runtime_seconds=900,
        session_recording_enabled=True,
        owner_approval_provided=True,
    )

    assert response["next_action"] == "create_bound_runtime_session"
    assert captured == {
        "command": "deployed-virtual-runtime-service-decision",
        "payload": {
            "operation": "create_cloud_session",
            "tenant_id": "t1",
            "workspace_id": "w1",
            "deployed_agent_id": "agent1",
            "studio_agent_mode": "cloud_computer_agent",
            "runtime_provider_id": "browserbase",
            "privacy_contract_valid": True,
            "computer_safety_contract_valid": True,
            "allowed_domain_count": 1,
            "daily_budget_usd_cents": 100,
            "session_timeout_seconds": 300,
            "max_runtime_seconds": 900,
            "session_recording_enabled": True,
            "owner_approval_provided": True,
        },
    }


def test_approval_requirement_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "require_approval",
            "approval_required": True,
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.approval_requirement(
        capability="shell",
        decision="require_approval",
        risk_level="high",
        action_class="execute",
        target_summary="npm install",
    )

    assert response["approval_required"] is True
    assert captured == {
        "command": "approval-requirement",
        "payload": {
            "capability": "shell",
            "decision": "require_approval",
            "risk_level": "high",
            "action_class": "execute",
            "target_summary": "npm install",
        },
    }


def test_artifact_policy_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "register_artifact",
            "artifact": {"retention_class": "short"},
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.artifact_policy(
        action="register",
        path="outputs/report.txt",
        size_bytes=128,
    )

    assert response["artifact"]["retention_class"] == "short"
    assert response["next_action"] == "register_artifact"
    assert captured == {
        "command": "artifact-policy",
        "payload": {
            "action": "register",
            "path": "outputs/report.txt",
            "size_bytes": 128,
        },
    }


def test_authorize_request_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "approval_required": False,
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.authorize_request(
        {"autonomy_mode": "yolo"},
        capability="network_access",
        action_class="network",
        payload={"url": "https://example.com"},
        requested_domain="example.com",
        target_summary="example.com",
        kill_switch_enabled=False,
        incident_mode="active",
    )

    assert response["decision"] == "allow"
    assert captured == {
        "command": "authorize-request",
        "payload": {
            "policy": {"autonomy_mode": "yolo"},
            "capability": "network_access",
            "action_class": "network",
            "payload": {"url": "https://example.com"},
            "requested_domain": "example.com",
            "target_summary": "example.com",
            "kill_switch_enabled": False,
            "incident_mode": "active",
        },
    }


def test_safe_mode_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": False,
            "decision": "block",
            "reason": "kill_switch_enabled",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.safe_mode_decision(
        kill_switch_enabled=True,
        capability="read_files",
    )

    assert response["decision"] == "block"
    assert captured == {
        "command": "safe-mode-decision",
        "payload": {
            "kill_switch_enabled": True,
            "capability": "read_files",
        },
    }


def test_thread_record_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "create_thread_turn",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.thread_record_decision(
        operation="create_turn",
        tenant_id="t1",
        workspace_id="w1",
        thread_id="thread1",
        actor_role="member",
        content="hello",
    )

    assert response["next_action"] == "create_thread_turn"
    assert captured == {
        "command": "thread-record-decision",
        "payload": {
            "operation": "create_turn",
            "tenant_id": "t1",
            "workspace_id": "w1",
            "thread_id": "thread1",
            "actor_role": "member",
            "content": "hello",
        },
    }


def test_execution_plan_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "execution": {"sandbox_type": "docker"},
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.execution_plan(
        command="python3 script.py",
        sandbox_required=True,
        max_runtime_seconds=120,
    )

    assert response["execution"]["sandbox_type"] == "docker"
    assert captured == {
        "command": "execution-plan",
        "payload": {
            "command": "python3 script.py",
            "sandbox_required": True,
            "max_runtime_seconds": 120,
        },
    }


def test_authorize_execution_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "allow_tool_execution",
            "execution_plan": {"decision": "allow"},
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.authorize_execution(
        {"autonomy_mode": "yolo"},
        command="python3 script.py",
        max_runtime_seconds=120,
        kill_switch_enabled=False,
    )

    assert response["decision"] == "allow"
    assert response["next_action"] == "allow_tool_execution"
    assert captured == {
        "command": "authorize-execution",
        "payload": {
            "policy": {"autonomy_mode": "yolo"},
            "command": "python3 script.py",
            "capability": "shell_command",
            "action_class": "execute",
            "payload": {},
            "max_runtime_seconds": 120,
            "kill_switch_enabled": False,
        },
    }


def test_execution_plan_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "allow_tool_execution",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.execution_plan(command="python3 script.py", sandbox_type="docker")

    assert response["decision"] == "allow"
    assert response["next_action"] == "allow_tool_execution"
    assert captured == {
        "command": "execution-plan",
        "payload": {
            "command": "python3 script.py",
            "sandbox_type": "docker",
        },
    }


def test_run_record_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "update_live_run_if_version_matches",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.run_record_decision(
        operation="persist_snapshot",
        run_id="run1",
        workspace_id="w1",
        tenant_id="t1",
        state="running",
        expected_version=2,
    )

    assert response["next_action"] == "update_live_run_if_version_matches"
    assert captured == {
        "command": "run-record-decision",
        "payload": {
            "operation": "persist_snapshot",
            "run_id": "run1",
            "workspace_id": "w1",
            "tenant_id": "t1",
            "state": "running",
            "expected_version": 2,
        },
    }


def test_run_approval_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "resolve_run_approval",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.run_approval_decision(
        operation="resolve_approval",
        tenant_id="t1",
        workspace_id="w1",
        approval_id="appr1",
        run_id="run1",
        actor_role="member",
        actor_user_id="user1",
        owner_user_id="user1",
        decision="approved",
    )

    assert response["next_action"] == "resolve_run_approval"
    assert captured == {
        "command": "run-approval-decision",
        "payload": {
            "operation": "resolve_approval",
            "tenant_id": "t1",
            "workspace_id": "w1",
            "approval_id": "appr1",
            "run_id": "run1",
            "actor_role": "member",
            "actor_user_id": "user1",
            "owner_user_id": "user1",
            "decision": "approved",
        },
    }


def test_outbox_delivery_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "claim_due_outbox_events",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.outbox_delivery_decision(
        operation="claim_due",
        claimed_by="worker1",
        limit=25,
    )

    assert response["next_action"] == "claim_due_outbox_events"
    assert captured == {
        "command": "outbox-delivery-decision",
        "payload": {
            "operation": "claim_due",
            "claimed_by": "worker1",
            "limit": 25,
        },
    }


def test_runtime_attachment_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "ensure_self_hosted_node_gate",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.runtime_attachment_decision(
        operation="self_hosted_gate",
        tenant_id="t1",
        workspace_id="w1",
        attachment_kind="self_hosted_business_node",
        runtime_profile_id="profile1",
        online=True,
        healthy=True,
        control_state="active",
    )

    assert response["next_action"] == "ensure_self_hosted_node_gate"
    assert captured == {
        "command": "runtime-attachment-decision",
        "payload": {
            "operation": "self_hosted_gate",
            "tenant_id": "t1",
            "workspace_id": "w1",
            "attachment_kind": "self_hosted_business_node",
            "runtime_profile_id": "profile1",
            "online": True,
            "healthy": True,
            "control_state": "active",
        },
    }


def test_virtual_computer_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "validate_computer_use_action_payload",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.virtual_computer_decision(
        operation="action_payload",
        computer_action="click",
        action_args={"x": 10, "y": 20},
    )

    assert response["next_action"] == "validate_computer_use_action_payload"
    assert captured == {
        "command": "virtual-computer-decision",
        "payload": {
            "operation": "action_payload",
            "computer_action": "click",
            "action_args": {"x": 10, "y": 20},
        },
    }


def test_execution_outcome_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "status": "succeeded",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.execution_outcome(
        exit_code=0,
        stdout="done",
        stderr="",
    )

    assert response["status"] == "succeeded"
    assert captured == {
        "command": "execution-outcome",
        "payload": {
            "exit_code": 0,
            "stdout": "done",
            "stderr": "",
        },
    }


def test_gateway_protocol_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "dispatch_tool_invoke",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.gateway_protocol_decision(
        message_type="tool.invoke",
        session_id="sess-1",
        workspace_id="ws-1",
        tool_name="computer_control.click",
        payload={"arguments": {"x": 1}},
    )

    assert response["next_action"] == "dispatch_tool_invoke"
    assert captured == {
        "command": "gateway-protocol-decision",
        "payload": {
            "message_type": "tool.invoke",
            "session_id": "sess-1",
            "workspace_id": "ws-1",
            "tool_name": "computer_control.click",
            "payload": {"arguments": {"x": 1}},
        },
    }


def test_gateway_action_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "return_health",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.gateway_action_decision(operation="health_check")

    assert response["next_action"] == "return_health"
    assert captured == {
        "command": "gateway-action-decision",
        "payload": {"operation": "health_check"},
    }


def test_gateway_frame_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "accept_gateway_connect",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.gateway_frame_decision(
        kind="request",
        type="gateway.connect",
        id="connect1",
        first_frame=True,
        protocol_version="v1alpha2",
    )

    assert response["next_action"] == "accept_gateway_connect"
    assert captured == {
        "command": "gateway-frame-decision",
        "payload": {
            "kind": "request",
            "type": "gateway.connect",
            "id": "connect1",
            "first_frame": True,
            "protocol_version": "v1alpha2",
        },
    }


def test_gateway_service_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "dispatch_gateway_operation",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.gateway_service_decision(
        operation="tool_execute",
        tenant_id="t1",
        workspace_id="w1",
        actor_id="u1",
        gateway_id="gw1",
        session_id="s1",
        run_id="run1",
        protocol_version="v1alpha2",
        approval_provided=True,
        approval_memory_hit=True,
    )

    assert response["next_action"] == "dispatch_gateway_operation"
    assert captured == {
        "command": "gateway-service-decision",
        "payload": {
            "operation": "tool_execute",
            "tenant_id": "t1",
            "workspace_id": "w1",
            "actor_id": "u1",
            "gateway_id": "gw1",
            "session_id": "s1",
            "run_id": "run1",
            "protocol_version": "v1alpha2",
            "approval_provided": True,
            "approval_memory_hit": True,
        },
    }


def test_gateway_state_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "issue_gateway_session",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.gateway_state_decision(
        operation="issue_session",
        tenant_id="t1",
        workspace_id="w1",
        gateway_id="gw1",
        registration_status="active",
    )

    assert response["next_action"] == "issue_gateway_session"
    assert captured == {
        "command": "gateway-state-decision",
        "payload": {
            "operation": "issue_session",
            "tenant_id": "t1",
            "workspace_id": "w1",
            "gateway_id": "gw1",
            "registration_status": "active",
        },
    }


def test_heartbeat_snapshot_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "health": "healthy",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.heartbeat_snapshot(
        workspace_id="w1",
        queue={"queued_count": 0},
        scheduler={"enabled": True},
    )

    assert response["health"] == "healthy"
    assert captured == {
        "command": "heartbeat-snapshot",
        "payload": {
            "workspace_id": "w1",
            "queue": {"queued_count": 0},
            "scheduler": {"enabled": True},
        },
    }


def test_local_worker_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "read_local_queue",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.local_worker_decision(
        operation="get_queue",
        workspace_id="w1",
    )

    assert response["next_action"] == "read_local_queue"
    assert captured == {
        "command": "local-worker-decision",
        "payload": {
            "operation": "get_queue",
            "workspace_id": "w1",
        },
    }


def test_machine_lease_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "operation": "acquire",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.machine_lease_decision(
        operation="acquire",
        holder_id="worker-1",
        active_lease_count=0,
        max_concurrent_leases=1,
    )

    assert response["operation"] == "acquire"
    assert captured == {
        "command": "machine-lease-decision",
        "payload": {
            "operation": "acquire",
            "holder_id": "worker-1",
            "active_lease_count": 0,
            "max_concurrent_leases": 1,
        },
    }


def test_process_lifecycle_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "spawn",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.process_lifecycle_decision(
        operation="start",
        run_id="run-1",
        status="queued",
    )

    assert response["next_action"] == "spawn"
    assert captured == {
        "command": "process-lifecycle-decision",
        "payload": {
            "operation": "start",
            "run_id": "run-1",
            "status": "queued",
        },
    }


def test_queue_transition_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_status": "running",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.queue_transition_decision(
        operation="claim",
        item_id="run-1",
        requester_id="worker-1",
        status="queued",
    )

    assert response["next_status"] == "running"
    assert captured == {
        "command": "queue-transition-decision",
        "payload": {
            "operation": "claim",
            "item_id": "run-1",
            "requester_id": "worker-1",
            "status": "queued",
        },
    }


def test_state_transition_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_state": "running",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.state_transition_decision(
        operation="start",
        entity_id="run-1",
        current_state="queued",
    )

    assert response["next_state"] == "running"
    assert captured == {
        "command": "state-transition-decision",
        "payload": {
            "operation": "start",
            "entity_id": "run-1",
            "current_state": "queued",
        },
    }


def test_session_lifecycle_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "continue",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.session_lifecycle_decision(
        operation="turn",
        session_id="s1",
        preset="standard",
        turn_count=2,
    )

    assert response["next_action"] == "continue"
    assert captured == {
        "command": "session-lifecycle-decision",
        "payload": {
            "operation": "turn",
            "session_id": "s1",
            "preset": "standard",
            "turn_count": 2,
        },
    }


def test_scheduler_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "wake",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.scheduler_decision(
        operation="wake",
        current_hour=12,
        wake_mode="now",
    )

    assert response["next_action"] == "wake"
    assert captured == {
        "command": "scheduler-decision",
        "payload": {
            "operation": "wake",
            "current_hour": 12,
            "wake_mode": "now",
        },
    }


def test_run_api_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "list_runs",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.run_api_decision(
        operation="list_runs",
        workspace_id="w1",
        tenant_id="t1",
    )

    assert response["next_action"] == "list_runs"
    assert captured == {
        "command": "run-api-decision",
        "payload": {
            "operation": "list_runs",
            "workspace_id": "w1",
            "tenant_id": "t1",
        },
    }


def test_run_preparation_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "prepare_run_request",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.run_preparation_decision(
        operation="prepare_run_start",
        workspace_id="w1",
        tenant_id="t1",
    )

    assert response["next_action"] == "prepare_run_request"
    assert captured == {
        "command": "run-preparation-decision",
        "payload": {
            "operation": "prepare_run_start",
            "workspace_id": "w1",
            "tenant_id": "t1",
        },
    }


def test_run_routing_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "write_execution_boundary_metadata",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.run_routing_decision(
        operation="execution_boundary",
        workspace_id="w1",
        execution_target="cloud",
    )

    assert response["next_action"] == "write_execution_boundary_metadata"
    assert captured == {
        "command": "run-routing-decision",
        "payload": {
            "operation": "execution_boundary",
            "workspace_id": "w1",
            "execution_target": "cloud",
        },
    }


def test_run_service_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "dispatch_run_to_runtime",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.run_service_decision(
        operation="dispatch",
        tenant_id="t1",
        workspace_id="w1",
        actor_id="u1",
        run_id="run1",
        thread_id="thread1",
        idempotency_key="idem1",
    )

    assert response["next_action"] == "dispatch_run_to_runtime"
    assert captured == {
        "command": "run-service-decision",
        "payload": {
            "operation": "dispatch",
            "tenant_id": "t1",
            "workspace_id": "w1",
            "actor_id": "u1",
            "run_id": "run1",
            "thread_id": "thread1",
            "idempotency_key": "idem1",
        },
    }


def test_run_trigger_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "execute_scheduled_run",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.run_trigger_decision(
        operation="trigger_schedule_now",
        workspace_id="w1",
        schedule_id="s1",
    )

    assert response["next_action"] == "execute_scheduled_run"
    assert captured == {
        "command": "run-trigger-decision",
        "payload": {
            "operation": "trigger_schedule_now",
            "workspace_id": "w1",
            "schedule_id": "s1",
        },
    }


def test_run_orchestration_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_status": "queued",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.run_orchestration_decision(
        operation="create",
        workspace_id="w1",
        prompt="Do work",
    )

    assert response["next_status"] == "queued"
    assert captured == {
        "command": "run-orchestration-decision",
        "payload": {
            "operation": "create",
            "workspace_id": "w1",
            "prompt": "Do work",
        },
    }


def test_runtime_binding_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "next_action": "skip_runtime_binding",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.runtime_binding_decision(
        operation="ensure_runtime_session",
        deployed_agent_id="agent1",
        tenant_id="t1",
        workspace_id="w1",
        session_id="s1",
        studio_agent_mode="text",
    )

    assert response["next_action"] == "skip_runtime_binding"
    assert captured == {
        "command": "runtime-binding-decision",
        "payload": {
            "operation": "ensure_runtime_session",
            "deployed_agent_id": "agent1",
            "tenant_id": "t1",
            "workspace_id": "w1",
            "session_id": "s1",
            "studio_agent_mode": "text",
        },
    }


def test_runtime_action_decision_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "allow",
            "runtime_action": "open_url",
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.runtime_action_decision(
        runtime_session_binding="cloud_computer_agent",
        studio_agent_mode="cloud_computer",
        connector_id="browser",
        action_id="navigate",
        url="https://example.com",
    )

    assert response["runtime_action"] == "open_url"
    assert captured == {
        "command": "runtime-action-decision",
        "payload": {
            "runtime_session_binding": "cloud_computer_agent",
            "studio_agent_mode": "cloud_computer",
            "connector_id": "browser",
            "action_id": "navigate",
            "url": "https://example.com",
        },
    }


def test_validate_policy_passes_scope_request_fields(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {"ok": True, "decision": "allow", "reason": "policy_valid"}

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    client.validate_policy(
        {"autonomy_mode": "yolo"},
        capability="network_access",
        requested_domain="example.com",
        requested_path="/workspace/file.txt",
    )

    assert captured == {
        "command": "validate-policy",
        "payload": {
            "policy": {"autonomy_mode": "yolo"},
            "capability": "network_access",
            "requested_domain": "example.com",
            "requested_path": "/workspace/file.txt",
        },
    }


def test_inspect_secret_reference_uses_allowlisted_command(monkeypatch):
    captured = {}

    def fake_run(command, payload):
        captured["command"] = command
        captured["payload"] = payload
        return {
            "ok": True,
            "decision": "require_approval",
            "secret_like": True,
        }

    monkeypatch.setattr(client, "run_runtime_kernel", fake_run)

    response = client.inspect_secret_reference(
        name="OPENAI_API_KEY",
        value="sk-test",
        action_class="read",
    )

    assert response["decision"] == "require_approval"
    assert captured == {
        "command": "inspect-secret-reference",
        "payload": {
            "name": "OPENAI_API_KEY",
            "value": "sk-test",
            "action_class": "read",
        },
    }


def test_runtime_kernel_invalid_json_fails_closed(monkeypatch, tmp_path):
    kernel = tmp_path / "kernel"
    kernel.write_text("#!/bin/sh\nprintf 'not-json'\n", encoding="utf-8")
    os.chmod(kernel, 0o700)
    monkeypatch.setattr(client, "runtime_kernel_binary", lambda: kernel)

    response = client.run_runtime_kernel("validate-policy", {"policy": {"autonomy_mode": "yolo"}})

    assert response["ok"] is False
    assert response["decision"] == "block"
    assert response["reason"] == "runtime_kernel_invalid_json"


def test_runtime_kernel_missing_decision_fails_closed(monkeypatch, tmp_path):
    kernel = tmp_path / "kernel"
    kernel.write_text("#!/bin/sh\nprintf '{\"ok\":true}'\n", encoding="utf-8")
    os.chmod(kernel, 0o700)
    monkeypatch.setattr(client, "runtime_kernel_binary", lambda: kernel)

    response = client.run_runtime_kernel("validate-policy", {"policy": {"autonomy_mode": "yolo"}})

    assert response["ok"] is False
    assert response["decision"] == "block"
    assert response["reason"] == "runtime_kernel_invalid_response"


def test_normalize_kernel_decision_adds_command_and_decision_id():
    normalized = client.normalize_kernel_decision(
        "run-service-decision",
        {
            "ok": True,
            "decision": "allow",
            "operation": "create",
            "reason": "permitted",
            "next_action": "persist_run",
        },
    )

    assert normalized["command"] == "run-service-decision"
    assert normalized["decision_id"].startswith("rkd_")
    assert normalized["operation"] == "create"
    assert normalized["next_action"] == "persist_run"
    assert normalized["audit_visibility"] == "standard"


def test_build_parity_fixture_records_decision_metadata():
    fixture = client.build_parity_fixture(
        "run-service-decision",
        {"operation": "create", "workspace_id": "w1"},
        {
            "ok": True,
            "decision": "allow",
            "operation": "create",
            "reason": "permitted",
        },
        legacy_response={"decision": "allow", "reason": "legacy_permitted"},
        notes="parity proof seed",
    )

    assert fixture["fixture_version"] == client.PARITY_FIXTURE_VERSION
    assert fixture["command"] == "run-service-decision"
    assert fixture["parity_fixture_family"] == "run_service"
    assert fixture["rust_decision"]["decision_id"].startswith("rkd_")
    assert fixture["legacy_decision"]["decision"] == "allow"
    assert fixture["notes"] == "parity proof seed"


def test_kernel_manifest_entries_include_contract_metadata():
    entry = client.KERNEL_OWNERSHIP_MANIFEST["run-service-decision"]

    assert entry["owner"] == "rust"
    assert entry["enforcement_status"] == "active_enforcement"
    assert entry["wrapper"] == "run_service_decision"
    assert entry["rust_module"] == "run_service"
    assert entry["python_test_hint"] == "server_modules/tests/test_rust_runtime_kernel_client.py"
    assert entry["rust_test_hint"] == "empyralis-runtime-kernel/src/run_service.rs"
    assert entry["parity_fixture_family"] == "run_service"


def test_runtime_kernel_nonzero_exit_fails_closed(monkeypatch, tmp_path):
    kernel = tmp_path / "kernel"
    kernel.write_text("#!/bin/sh\nprintf '{\"reason\":\"forced\"}'\nexit 2\n", encoding="utf-8")
    os.chmod(kernel, 0o700)
    monkeypatch.setattr(client, "runtime_kernel_binary", lambda: kernel)

    response = client.run_runtime_kernel("validate-policy", {"policy": {"autonomy_mode": "yolo"}})

    assert response["ok"] is False
    assert response["decision"] == "block"
    assert response["reason"] == "forced"


def test_redact_diagnostics_uses_json_payload(monkeypatch, tmp_path):
    capture = tmp_path / "redact-diagnostics.payload"
    kernel = tmp_path / "kernel"
    kernel.write_text(
        "#!/bin/sh\ncat > \"$1.payload\"\nprintf '{\"ok\":true,\"decision\":\"allow\",\"next_action\":\"return_redacted_diagnostics\",\"redacted\":{\"safe\":true}}'\n",
        encoding="utf-8",
    )
    os.chmod(kernel, 0o700)
    monkeypatch.setattr(client, "runtime_kernel_binary", lambda: kernel)
    monkeypatch.chdir(tmp_path)

    response = client.run_runtime_kernel("redact-diagnostics", {"data": {"token": "secret"}})

    assert response["ok"] is True
    assert response["decision"] == "allow"
    assert response["next_action"] == "return_redacted_diagnostics"
    assert response["redacted"] == {"safe": True}
    assert response["command"] == "redact-diagnostics"
    assert response["operation"] == "redact-diagnostics"
    assert response["approval_required"] is False
    assert response["audit_visibility"] == "standard"
    assert response["cacheable"] is False
    assert json.loads(capture.read_text(encoding="utf-8")) == {"data": {"token": "secret"}}

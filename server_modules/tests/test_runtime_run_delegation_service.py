import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from server_modules.agent_trace_service import TraceContext
from server_modules import runtime_run_delegation_service


class _Child:
    def __init__(self, *, agent_role: str, user_goal: str, business_plan: str = "", metadata=None) -> None:
        self.agent_role = agent_role
        self.user_goal = user_goal
        self.business_plan = business_plan
        self.metadata = metadata if metadata is not None else {}


class _DelegationPayload:
    def __init__(self, children, note: str = "") -> None:
        self.children = children
        self.note = note

    def validate_fields(self) -> None:
        return None


class _AutoDelegationPayload:
    def __init__(self, *, max_children: int = 3, note: str = "") -> None:
        self.max_children = max_children
        self.note = note

    def validate_fields(self) -> None:
        return None


class _RetryPayload:
    def __init__(self, *, failed_run_ids=None, note: str = "") -> None:
        self.failed_run_ids = failed_run_ids
        self.note = note

    def validate_fields(self) -> None:
        return None


def _parent_snapshot(agent_role: str = "orchestrator") -> dict:
    return {
        "run_id": "parent-1",
        "agent_role": agent_role,
        "delegation_root_run_id": None,
        "context": {"metadata": {"agent_role": agent_role}},
    }


def _parent_snapshot_at_subagent_depth(depth: int, agent_role: str = "orchestrator") -> dict:
    return {
        "run_id": "parent-1",
        "agent_role": agent_role,
        "delegation_root_run_id": None,
        "context": {"metadata": {"agent_role": agent_role, "subagent_depth": depth}},
    }


class RuntimeRunDelegationServiceTests(unittest.TestCase):
    @staticmethod
    def _rust_decision_side_effect(command, payload, allow_approval_required=False):
        if payload["operation"] == "retry":
            return {
                "ok": True,
                "decision": "allow",
                "operation": "retry",
                "next_action": "retry_run",
            }
        if payload["operation"] == "delegation_child":
            return {
                "ok": True,
                "decision": "allow",
                "operation": "delegation_child",
                "next_action": "create_delegated_child_run",
            }
        if payload["operation"] == "delegation_merge":
            return {
                "ok": True,
                "decision": "require_approval",
                "operation": "delegation_merge",
                "next_action": "retry_failed_children",
            }
        raise AssertionError(payload["operation"])

    def test_build_retry_failed_delegation_callbacks_includes_retry_specific_entries(self):
        callbacks = runtime_run_delegation_service.build_retry_failed_delegation_callbacks(
            lookup_run_snapshot=lambda run_id: _parent_snapshot(),
            enforce_run_owner_access=lambda current_user, snapshot: None,
            normalize_agent_role=lambda role: str(role or "").strip().lower(),
            find_run_relationships=lambda parent_run_id, snapshot: (snapshot, []),
            normalize_run_id_token=lambda value: str(value or "").strip() or None,
            parse_utc_ts=lambda value: None,
            build_retry_child_payload=lambda parent_snapshot, child, note=None: {},
            build_delegated_run_request=lambda *args, **kwargs: {},
            execute_system_run_start_request_via_turn_runtime=lambda *args, **kwargs: {},
            stamp_request_owner_fn=lambda payload: payload,
            run_execution_services=lambda: object(),
            refresh_parent_delegation_state=lambda run_id: None,
        )

        self.assertIn("find_run_relationships", callbacks)
        self.assertIn("build_retry_child_payload", callbacks)
        self.assertIn("build_delegated_run_request", callbacks)

    def test_delegate_run_children_rejects_orchestrator_target_role(self):
        with self.assertRaises(HTTPException):
            runtime_run_delegation_service.delegate_run_children(
                "parent-1",
                body=_DelegationPayload([_Child(agent_role="orchestrator", user_goal="bad")]),
                current_user={"user_id": "user-1"},
                lookup_run_snapshot=lambda run_id: _parent_snapshot(),
                enforce_run_owner_access=lambda current_user, snapshot: None,
                normalize_agent_role=lambda role: str(role or "").strip().lower(),
                build_delegated_run_request=lambda *args, **kwargs: {},
                execute_system_run_start_request_via_turn_runtime=lambda *args, **kwargs: {},
                stamp_request_owner_fn=lambda payload: payload,
                run_execution_services=lambda: object(),
                normalize_run_id_token=lambda value: str(value or "").strip() or None,
                refresh_parent_delegation_state=lambda run_id: None,
            )

    def test_delegate_run_children_blocked_at_depth_two(self):
        """STEP 6 / §1.4 (agent-identity plan): a run already at
        subagent_depth=1 (itself a subagent) may not delegate further --
        child_depth would be 2, over MAX_SUBAGENT_DEPTH_DEFAULT=1. Verifies
        the block happens BEFORE the Rust run-routing gate is even
        consulted and before any child run is created."""
        rust_gate_called = {"called": False}

        def _exploding_rust_call(command, payload, allow_approval_required=False):
            rust_gate_called["called"] = True
            raise AssertionError("Rust run-routing gate must not be reached once the depth cap denies")

        executed = {"called": False}
        with patch.object(
            runtime_run_delegation_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=_exploding_rust_call,
        ):
            with self.assertRaises(HTTPException) as raised:
                runtime_run_delegation_service.delegate_run_children(
                    "parent-1",
                    body=_DelegationPayload([_Child(agent_role="researcher", user_goal="good")]),
                    current_user={"user_id": "user-1"},
                    lookup_run_snapshot=lambda run_id: _parent_snapshot_at_subagent_depth(1),
                    enforce_run_owner_access=lambda current_user, snapshot: None,
                    normalize_agent_role=lambda role: str(role or "").strip().lower(),
                    build_delegated_run_request=lambda *args, **kwargs: {},
                    execute_system_run_start_request_via_turn_runtime=lambda *args, **kwargs: executed.update({"called": True}),
                    stamp_request_owner_fn=lambda payload: payload,
                    run_execution_services=lambda: object(),
                    normalize_run_id_token=lambda value: str(value or "").strip() or None,
                    refresh_parent_delegation_state=lambda run_id: None,
                )

        self.assertEqual(raised.exception.status_code, 403)
        self.assertIn("depth", str(raised.exception.detail).lower())
        self.assertFalse(rust_gate_called["called"])
        self.assertFalse(executed["called"])

    def test_auto_delegate_run_children_emits_routing_log_and_returns_created_items(self):
        routing_logs = []
        created_requests = []

        with patch.object(
            runtime_run_delegation_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._rust_decision_side_effect,
        ):
            payload = runtime_run_delegation_service.auto_delegate_run_children(
                "parent-1",
                request_payload=_AutoDelegationPayload(note=""),
                current_user={"user_id": "user-1"},
                lookup_run_snapshot=lambda run_id: _parent_snapshot(),
                enforce_run_owner_access=lambda current_user, snapshot: None,
                normalize_agent_role=lambda role: str(role or "").strip().lower(),
                build_auto_delegation_plan=lambda snapshot, max_children=3: [
                    {
                        "agent_role": "researcher",
                        "user_goal": "Inspect logs",
                        "metadata": {
                            "auto_delegation_rule": "logs",
                            "auto_delegation_source": "keyword",
                            "auto_delegation_reason": "contains log triage",
                        },
                    }
                ],
                emit_auto_delegation_routing_log=lambda parent_run_id, plan, strategy, reason: routing_logs.append(
                    (parent_run_id, strategy, reason, len(plan))
                ),
                build_delegated_run_request=lambda snapshot, child, note=None: {"child": child, "note": note},
                execute_system_run_start_request_via_turn_runtime=lambda delegated_req, **kwargs: created_requests.append(delegated_req) or {"run_id": "child-1"},
                stamp_request_owner_fn=lambda payload: payload,
                run_execution_services=lambda: object(),
                normalize_run_id_token=lambda value: str(value or "").strip() or None,
                refresh_parent_delegation_state=lambda run_id: None,
            )

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["note"], "Auto-planned by orchestrator rules.")
        self.assertEqual(payload["items"][0]["auto_delegation_rule"], "logs")
        self.assertEqual(created_requests[0]["note"], "Auto-planned by orchestrator rules.")
        self.assertEqual(routing_logs, [("parent-1", "keyword", "contains log triage", 1)])

    def test_auto_delegate_run_children_emits_trace_plan_and_delegation_events(self):
        trace_context = TraceContext(
            trace_id="trace-1",
            workspace_id="default",
            tenant_id="default",
            thread_id="thread-1",
            run_id="parent-1",
            root_agent_id="sage",
        )
        parent_snapshot = {
            "run_id": "parent-1",
            "agent_role": "orchestrator",
            "delegation_root_run_id": None,
            "context": {"workspace_id": "default", "tenant_id": "default", "metadata": {"agent_role": "orchestrator", "trace_id": "trace-1"}},
        }

        with patch("server_modules.runtime_run_delegation_service.run_async_tool_call", side_effect=lambda coro: asyncio.run(coro)), patch.object(
            runtime_run_delegation_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._rust_decision_side_effect,
        ), patch(
            "server_modules.runtime_run_delegation_service.agent_trace_service.resume_trace",
            new=AsyncMock(return_value=trace_context),
        ), patch(
            "server_modules.runtime_run_delegation_service.agent_trace_service.emit_plan_started",
            new=AsyncMock(return_value="evt-plan-start"),
        ) as plan_started_mock, patch(
            "server_modules.runtime_run_delegation_service.agent_trace_service.emit_plan_item",
            new=AsyncMock(return_value="evt-plan-item"),
        ) as plan_item_mock, patch(
            "server_modules.runtime_run_delegation_service.agent_trace_service.emit_plan_item_updated",
            new=AsyncMock(return_value="evt-plan-update"),
        ) as plan_item_updated_mock, patch(
            "server_modules.runtime_run_delegation_service.agent_trace_service.emit_delegation_started",
            new=AsyncMock(return_value="evt-delegation-start"),
        ) as delegation_started_mock, patch(
            "server_modules.runtime_run_delegation_service.agent_trace_service.emit_delegation_finished",
            new=AsyncMock(return_value="evt-delegation-finish"),
        ) as delegation_finished_mock:
            payload = runtime_run_delegation_service.auto_delegate_run_children(
                "parent-1",
                request_payload=_AutoDelegationPayload(note=""),
                current_user={"user_id": "user-1"},
                lookup_run_snapshot=lambda run_id: parent_snapshot,
                enforce_run_owner_access=lambda current_user, snapshot: None,
                normalize_agent_role=lambda role: str(role or "").strip().lower(),
                build_auto_delegation_plan=lambda snapshot, max_children=3: [
                    {
                        "agent_role": "researcher",
                        "user_goal": "Inspect logs",
                        "metadata": {
                            "auto_delegation_rule": "logs",
                            "auto_delegation_source": "keyword",
                            "auto_delegation_reason": "contains log triage",
                        },
                    }
                ],
                emit_auto_delegation_routing_log=None,
                build_delegated_run_request=lambda snapshot, child, note=None: {"child": child, "note": note},
                execute_system_run_start_request_via_turn_runtime=lambda delegated_req, **kwargs: {"run_id": "child-1"},
                stamp_request_owner_fn=lambda payload: payload,
                run_execution_services=lambda: object(),
                normalize_run_id_token=lambda value: str(value or "").strip() or None,
                refresh_parent_delegation_state=lambda run_id: None,
            )

        self.assertEqual(payload["count"], 1)
        plan_started_mock.assert_awaited_once()
        plan_item_mock.assert_awaited_once()
        plan_item_updated_mock.assert_awaited_once()
        delegation_started_mock.assert_awaited_once()
        delegation_finished_mock.assert_awaited_once()

    def test_retry_failed_delegation_runs_retries_latest_failed_child_per_lineage(self):
        child_runs = [
            {
                "run_id": "child-1",
                "retry_root_run_id": "root-1",
                "status": "failed",
                "updated_at": "2026-04-05T00:00:00Z",
                "created_at": "2026-04-05T00:00:00Z",
            },
            {
                "run_id": "child-2",
                "retry_root_run_id": "root-1",
                "status": "completed",
                "updated_at": "2026-04-05T01:00:00Z",
                "created_at": "2026-04-05T01:00:00Z",
            },
            {
                "run_id": "child-3",
                "retry_root_run_id": "root-2",
                "status": "timeout",
                "updated_at": "2026-04-05T02:00:00Z",
                "created_at": "2026-04-05T02:00:00Z",
            },
        ]
        built_payloads = []

        with patch.object(
            runtime_run_delegation_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._rust_decision_side_effect,
        ):
            payload = runtime_run_delegation_service.retry_failed_delegation_runs(
                "parent-1",
                request_payload=_RetryPayload(),
                current_user={"user_id": "user-1"},
                lookup_run_snapshot=lambda run_id: _parent_snapshot(),
                enforce_run_owner_access=lambda current_user, snapshot: None,
                normalize_agent_role=lambda role: str(role or "").strip().lower(),
                find_run_relationships=lambda parent_run_id, snapshot: (snapshot, child_runs),
                normalize_run_id_token=lambda value: str(value or "").strip() or None,
                parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None,
                build_retry_child_payload=lambda parent_snapshot, child, note=None: built_payloads.append((child["run_id"], note)) or {
                    "agent_role": "researcher",
                    "user_goal": f"Retry {child['run_id']}",
                    "metadata": {
                        "retry_of_run_id": child["run_id"],
                        "retry_root_run_id": child.get("retry_root_run_id") or child["run_id"],
                        "retry_sequence": 1,
                    },
                },
                build_delegated_run_request=lambda snapshot, child, note=None: {"child": child, "note": note},
                execute_system_run_start_request_via_turn_runtime=lambda delegated_req, **kwargs: {"run_id": f"new-{delegated_req['child']['metadata']['retry_of_run_id']}"},
                stamp_request_owner_fn=lambda payload: payload,
                run_execution_services=lambda: object(),
                refresh_parent_delegation_state=lambda run_id: None,
            )

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["items"][0]["retry_of_run_id"], "child-3")
        self.assertEqual(payload["items"][0]["run_id"], "new-child-3")
        self.assertEqual(built_payloads, [("child-3", "Retry requested from orchestration summary.")])

    def test_retry_failed_delegation_wrong_retry_action_blocks_before_retry_build(self):
        built = {"called": False}
        child_runs = [
            {
                "run_id": "child-3",
                "retry_root_run_id": "root-2",
                "status": "timeout",
                "updated_at": "2026-04-05T02:00:00Z",
                "created_at": "2026-04-05T02:00:00Z",
            },
        ]

        def side_effect(command, payload, allow_approval_required=False):
            if payload["operation"] == "delegation_merge":
                return {
                    "ok": True,
                    "decision": "require_approval",
                    "operation": "delegation_merge",
                    "next_action": "retry_failed_children",
                }
            if payload["operation"] == "retry":
                return {
                    "ok": True,
                    "decision": "allow",
                    "operation": "retry",
                    "next_action": "dispatch_run",
                }
            raise AssertionError(payload["operation"])

        with patch.object(
            runtime_run_delegation_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=side_effect,
        ):
            with self.assertRaises(HTTPException) as raised:
                runtime_run_delegation_service.retry_failed_delegation_runs(
                    "parent-1",
                    request_payload=_RetryPayload(),
                    current_user={"user_id": "user-1"},
                    lookup_run_snapshot=lambda run_id: _parent_snapshot(),
                    enforce_run_owner_access=lambda current_user, snapshot: None,
                    normalize_agent_role=lambda role: str(role or "").strip().lower(),
                    find_run_relationships=lambda parent_run_id, snapshot: (snapshot, child_runs),
                    normalize_run_id_token=lambda value: str(value or "").strip() or None,
                    parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None,
                    build_retry_child_payload=lambda parent_snapshot, child, note=None: built.update({"called": True}) or {},
                    build_delegated_run_request=lambda snapshot, child, note=None: {"child": child, "note": note},
                    execute_system_run_start_request_via_turn_runtime=lambda delegated_req, **kwargs: {"run_id": "new-child-3"},
                    stamp_request_owner_fn=lambda payload: payload,
                    run_execution_services=lambda: object(),
                    refresh_parent_delegation_state=lambda run_id: None,
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", str(raised.exception.detail))
        self.assertFalse(built["called"])

    def test_delegate_run_children_wrong_rust_next_action_blocks_before_child_creation(self):
        executed = {"called": False}
        with patch.object(
            runtime_run_delegation_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "operation": "delegation_child",
                "next_action": "merge_child_results",
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                runtime_run_delegation_service.delegate_run_children(
                    "parent-1",
                    body=_DelegationPayload([_Child(agent_role="researcher", user_goal="good")]),
                    current_user={"user_id": "user-1"},
                    lookup_run_snapshot=lambda run_id: _parent_snapshot(),
                    enforce_run_owner_access=lambda current_user, snapshot: None,
                    normalize_agent_role=lambda role: str(role or "").strip().lower(),
                    build_delegated_run_request=lambda *args, **kwargs: {},
                    execute_system_run_start_request_via_turn_runtime=lambda *args, **kwargs: executed.update({"called": True}),
                    stamp_request_owner_fn=lambda payload: payload,
                    run_execution_services=lambda: object(),
                    normalize_run_id_token=lambda value: str(value or "").strip() or None,
                    refresh_parent_delegation_state=lambda run_id: None,
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", str(raised.exception.detail))
        self.assertFalse(executed["called"])

    def test_retry_failed_delegation_wrong_merge_action_blocks_before_retry_build(self):
        built = {"called": False}
        child_runs = [
            {
                "run_id": "child-3",
                "retry_root_run_id": "root-2",
                "status": "timeout",
                "updated_at": "2026-04-05T02:00:00Z",
                "created_at": "2026-04-05T02:00:00Z",
            },
        ]
        with patch.object(
            runtime_run_delegation_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "operation": "delegation_merge",
                "next_action": "merge_child_results",
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                runtime_run_delegation_service.retry_failed_delegation_runs(
                    "parent-1",
                    request_payload=_RetryPayload(),
                    current_user={"user_id": "user-1"},
                    lookup_run_snapshot=lambda run_id: _parent_snapshot(),
                    enforce_run_owner_access=lambda current_user, snapshot: None,
                    normalize_agent_role=lambda role: str(role or "").strip().lower(),
                    find_run_relationships=lambda parent_run_id, snapshot: (snapshot, child_runs),
                    normalize_run_id_token=lambda value: str(value or "").strip() or None,
                    parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None,
                    build_retry_child_payload=lambda parent_snapshot, child, note=None: built.update({"called": True}) or {},
                    build_delegated_run_request=lambda snapshot, child, note=None: {"child": child, "note": note},
                    execute_system_run_start_request_via_turn_runtime=lambda delegated_req, **kwargs: {"run_id": "new-child-3"},
                    stamp_request_owner_fn=lambda payload: payload,
                    run_execution_services=lambda: object(),
                    refresh_parent_delegation_state=lambda run_id: None,
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", str(raised.exception.detail))
        self.assertFalse(built["called"])


    def test_retry_failed_delegation_runs_blocked_at_max_subagent_depth(self):
        """§1.4 cleanup: retry_failed_delegation_runs used to skip
        assert_subagent_spawn_allowed entirely, unlike delegate_run_children /
        auto_delegate_run_children -- so a depth-2+ retry could slip through
        even though a fresh delegate call at the same parent depth would be
        refused outright. Verifies the retry path is now equally gated, and
        that it is blocked BEFORE find_run_relationships is even consulted
        (a denied depth means the child relationships are irrelevant)."""
        relationships_called = {"called": False}

        def _find_run_relationships(parent_run_id, snapshot):
            relationships_called["called"] = True
            return snapshot, []

        with self.assertRaises(HTTPException) as raised:
            runtime_run_delegation_service.retry_failed_delegation_runs(
                "parent-1",
                request_payload=_RetryPayload(),
                current_user={"user_id": "user-1"},
                lookup_run_snapshot=lambda run_id: _parent_snapshot_at_subagent_depth(1),
                enforce_run_owner_access=lambda current_user, snapshot: None,
                normalize_agent_role=lambda role: str(role or "").strip().lower(),
                find_run_relationships=_find_run_relationships,
                normalize_run_id_token=lambda value: str(value or "").strip() or None,
                parse_utc_ts=lambda value: None,
                build_retry_child_payload=lambda parent_snapshot, child, note=None: {},
                build_delegated_run_request=lambda *args, **kwargs: {},
                execute_system_run_start_request_via_turn_runtime=lambda *args, **kwargs: {},
                stamp_request_owner_fn=lambda payload: payload,
                run_execution_services=lambda: object(),
                refresh_parent_delegation_state=lambda run_id: None,
            )

        self.assertEqual(raised.exception.status_code, 403)
        self.assertIn("depth", str(raised.exception.detail).lower())
        self.assertFalse(relationships_called["called"])

    def test_retry_failed_delegation_runs_allowed_at_depth_zero(self):
        """Sanity check for the depth-cap fix above: a depth-0 (root)
        orchestrator's retry must still go through -- the new gate only
        denies once the CHILD depth would exceed max_subagent_depth()."""
        with patch.object(
            runtime_run_delegation_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._rust_decision_side_effect,
        ):
            payload = runtime_run_delegation_service.retry_failed_delegation_runs(
                "parent-1",
                request_payload=_RetryPayload(),
                current_user={"user_id": "user-1"},
                lookup_run_snapshot=lambda run_id: _parent_snapshot_at_subagent_depth(0),
                enforce_run_owner_access=lambda current_user, snapshot: None,
                normalize_agent_role=lambda role: str(role or "").strip().lower(),
                find_run_relationships=lambda parent_run_id, snapshot: (
                    snapshot,
                    [
                        {
                            "run_id": "child-3",
                            "retry_root_run_id": "root-2",
                            "status": "timeout",
                            "updated_at": "2026-04-05T02:00:00Z",
                            "created_at": "2026-04-05T02:00:00Z",
                        },
                    ],
                ),
                normalize_run_id_token=lambda value: str(value or "").strip() or None,
                parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None,
                build_retry_child_payload=lambda parent_snapshot, child, note=None: {
                    "agent_role": "researcher",
                    "user_goal": f"Retry {child['run_id']}",
                    "metadata": {
                        "retry_of_run_id": child["run_id"],
                        "retry_root_run_id": child.get("retry_root_run_id") or child["run_id"],
                        "retry_sequence": 1,
                    },
                },
                build_delegated_run_request=lambda snapshot, child, note=None: {"child": child, "note": note},
                execute_system_run_start_request_via_turn_runtime=lambda delegated_req, **kwargs: {"run_id": "new-child-3"},
                stamp_request_owner_fn=lambda payload: payload,
                run_execution_services=lambda: object(),
                refresh_parent_delegation_state=lambda run_id: None,
            )
        self.assertEqual(payload["count"], 1)

    def test_delegation_child_decision_defaults_workflow_turn_depth_to_governed_constant(self):
        """§1.4 cleanup: max_workflow_turn_depth used to hard-fall back to
        the literal 999999 in _enforce_delegation_child_decision, which made
        the Rust run-routing gate's depth check a no-op for delegation (a
        999999-turn ceiling never triggers). Confirms the fallback is now
        run_service.MAX_WORKFLOW_TURN_DEPTH_DEFAULT (30), reused rather than
        another made-up literal."""
        from server_modules import run_service

        captured_payloads = []

        def _capture(command, payload, allow_approval_required=False):
            captured_payloads.append(payload)
            return self._rust_decision_side_effect(command, payload, allow_approval_required)

        with patch.object(
            runtime_run_delegation_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=_capture,
        ):
            runtime_run_delegation_service.delegate_run_children(
                "parent-1",
                body=_DelegationPayload([_Child(agent_role="researcher", user_goal="good")]),
                current_user={"user_id": "user-1"},
                lookup_run_snapshot=lambda run_id: _parent_snapshot(),
                enforce_run_owner_access=lambda current_user, snapshot: None,
                normalize_agent_role=lambda role: str(role or "").strip().lower(),
                build_delegated_run_request=lambda *args, **kwargs: {},
                execute_system_run_start_request_via_turn_runtime=lambda *args, **kwargs: {"run_id": "child-1"},
                stamp_request_owner_fn=lambda payload: payload,
                run_execution_services=lambda: object(),
                normalize_run_id_token=lambda value: str(value or "").strip() or None,
                refresh_parent_delegation_state=lambda run_id: None,
            )

        child_decision_payloads = [p for p in captured_payloads if p["operation"] == "delegation_child"]
        self.assertEqual(len(child_decision_payloads), 1)
        self.assertEqual(
            child_decision_payloads[0]["max_workflow_turn_depth"],
            run_service.MAX_WORKFLOW_TURN_DEPTH_DEFAULT,
        )
        self.assertNotEqual(child_decision_payloads[0]["max_workflow_turn_depth"], 999999)


class _FakeClock:
    """Deterministic monotonic clock + no-op sleep for testing the poll loop
    without ever really sleeping or racing wall-clock time."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += float(seconds)


class SpawnSubagentFromChatTurnTests(unittest.TestCase):
    """server_modules.runtime_run_delegation_service.spawn_subagent_from_chat_turn
    -- the chat-turn bridge into the real delegation engine (2026-07-24 ruling)."""

    def _fake_run_store(self):
        store: dict[str, dict] = {}

        def _lookup(run_id: str) -> dict:
            return dict(store.get(run_id) or {})

        return store, _lookup

    def _fake_create_call(self, store: dict, *, status: str = "completed", summary: str = "Done."):
        created_run_ids: list[str] = []

        def _execute(request, *, stamp_request_owner_fn, services, current_user=None):
            # Numbered off the SHARED store (not this closure's own counter)
            # so repeated calls across a test's loop never collide -- each
            # _fake_create_call() invocation gets its own empty
            # created_run_ids list, but they all write into the same store.
            run_id = f"child-{len(store) + 1}"
            created_run_ids.append(run_id)
            store[run_id] = {
                "run_id": run_id,
                "status": status,
                "result_summary": summary,
                # Deliberately included so the summary-only-contract test can
                # assert these never leak into the tool result.
                "events": [{"event": "step", "message": "secret intermediate reasoning"}],
                "context": {"metadata": dict(request.metadata or {})},
            }
            return {"run_id": run_id}

        return _execute, created_run_ids

    def _call(self, session_ctx, *, store=None, lookup=None, execute=None, **overrides):
        if store is None:
            store, lookup = self._fake_run_store()
        if execute is None:
            execute, _ = self._fake_create_call(store)
        kwargs = dict(
            task_description="Summarize the last 10 support tickets.",
            session_ctx=session_ctx,
            workspace_id="ws-1",
            tenant_id="default",
            owner_user_id="user-1",
            acting_agent_install_id="agent-pixel",
            enforce_delegation_child_decision_fn=lambda **kw: {"ok": True},
            build_delegated_run_request_fn=lambda parent_snapshot, child_payload, note=None: (
                __import__("server_modules.run_service", fromlist=["build_delegated_child_run_request"])
                .build_delegated_child_run_request(
                    parent_snapshot,
                    child_payload,
                    normalize_run_id_token=lambda v: str(v or "").strip() or None,
                    normalize_agent_role=lambda v: str(v or "").strip().lower(),
                    normalize_requested_max_iterations=lambda v: None,
                    valid_execution_targets={"cloud", "local", "auto"},
                    note=note,
                )
            ),
            execute_system_run_start_request_via_turn_runtime_fn=execute,
            run_execution_services_fn=lambda: object(),
            lookup_run_snapshot_fn=lookup,
            normalize_agent_role_fn=lambda v: str(v or "").strip().lower(),
            stamp_request_owner_fn=lambda req, current_user: req,
            sleep_fn=lambda seconds: None,
            monotonic_fn=lambda: 0.0,
        )
        kwargs.update(overrides)
        return runtime_run_delegation_service.spawn_subagent_from_chat_turn(**kwargs)

    def test_missing_task_description_is_refused_loudly(self):
        result = self._call({}, task_description="   ")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "missing_task_description")
        self.assertTrue(result["message"])

    def test_successful_spawn_returns_summary_only_contract(self):
        session_ctx: dict = {}
        result = self._call(session_ctx)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["summary"], "Done.")
        self.assertEqual(result["spawns_used"], 1)
        self.assertEqual(result["spawns_remaining"], 4)
        # The summary contract: only ok/run_id/status/summary/spawns_* --
        # never the child's raw events or context/transcript, even though
        # the fake run store above deliberately included both.
        self.assertEqual(
            set(result.keys()),
            {"ok", "run_id", "status", "summary", "spawns_used", "spawns_remaining"},
        )
        self.assertNotIn("events", result)
        self.assertNotIn("context", result)
        # session_ctx was mutated in place (by reference) -- the counter is
        # visible to the caller for the next spawn in the same turn.
        self.assertEqual(session_ctx[runtime_run_delegation_service.SUBAGENT_SPAWN_COUNT_SESSION_KEY], 1)

    def test_child_run_request_carries_real_parent_lineage_and_depth_stamp(self):
        # Uses the REAL run_service.build_delegated_child_run_request (see
        # build_delegated_run_request_fn above) -- proves the bridge really
        # drives the existing engine's own lineage/depth stamping, not a
        # parallel implementation of it.
        store, lookup = self._fake_run_store()
        execute, created_run_ids = self._fake_create_call(store)
        session_ctx: dict = {}
        result = self._call(session_ctx, store=store, lookup=lookup, execute=execute)
        self.assertTrue(result["ok"])
        child_run_id = created_run_ids[0]
        stamped_metadata = store[child_run_id]["context"]["metadata"]
        self.assertEqual(stamped_metadata["parent_run_id"], session_ctx["subagent_task_root_id"])
        self.assertEqual(stamped_metadata["delegation_root_run_id"], session_ctx["subagent_task_root_id"])
        self.assertEqual(stamped_metadata["delegated_by_role"], "orchestrator")
        # run_service.build_delegated_child_run_request stamps child depth =
        # parent depth (0, chat turn is root) + 1 -- this is the SAME
        # mechanism that would refuse a depth-2 grandchild if the engine's
        # own delegate_run_children were ever reachable from a run's own
        # tool loop (see the module docstring above this class).
        self.assertEqual(stamped_metadata["subagent_depth"], 1)

    def test_sixth_spawn_attempt_in_one_task_is_refused_with_explicit_message(self):
        session_ctx: dict = {}
        store, lookup = self._fake_run_store()
        results = []
        for _ in range(6):
            execute, _ = self._fake_create_call(store)
            results.append(self._call(session_ctx, store=store, lookup=lookup, execute=execute))
        for i in range(5):
            self.assertTrue(results[i]["ok"], msg=f"spawn {i + 1} should have succeeded")
        sixth = results[5]
        self.assertFalse(sixth["ok"])
        self.assertEqual(sixth["error"], "subagent_limit_reached")
        self.assertIn("5 sub-agent helpers", sixth["message"])
        self.assertIn("does not reset", sixth["message"])
        # The 6th attempt must be a pure refusal -- no 6th run was ever created.
        self.assertEqual(len(store), 5)
        self.assertEqual(session_ctx[runtime_run_delegation_service.SUBAGENT_SPAWN_COUNT_SESSION_KEY], 5)

    def test_a_finished_helper_does_not_free_a_slot(self):
        # Counting is lifetime/per-task, not concurrency: even though every
        # spawned child in this test finishes ("completed") before the next
        # spawn call, the 6th is still refused.
        session_ctx: dict = {}
        store, lookup = self._fake_run_store()
        last = None
        for _ in range(6):
            execute, _ = self._fake_create_call(store, status="completed")
            last = self._call(session_ctx, store=store, lookup=lookup, execute=execute)
        self.assertFalse(last["ok"])
        self.assertEqual(last["error"], "subagent_limit_reached")

    def test_subagent_attempting_to_spawn_is_refused(self):
        # session_ctx carries subagent_depth=1 -- this chat turn IS itself a
        # spawned child (defense in depth; see module docstring for why
        # nothing sets this today but the check exists anyway).
        session_ctx = {runtime_run_delegation_service.SUBAGENT_SPAWN_DEPTH_SESSION_KEY: 1}
        create_called = []

        def _execute(*args, **kwargs):
            create_called.append(True)
            return {"run_id": "should-not-exist"}

        result = self._call(session_ctx, execute=_execute)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "subagent_depth_exceeded")
        self.assertIn("cannot spawn further", result["message"])
        self.assertFalse(create_called, "a depth-exceeded refusal must never create a run")

    def test_disabled_kernel_decision_is_surfaced_as_explicit_refusal_not_an_exception(self):
        def _deny(**kwargs):
            raise HTTPException(status_code=409, detail="policy blocked this")

        create_called = []

        def _execute(*args, **kwargs):
            create_called.append(True)
            return {"run_id": "should-not-exist"}

        result = self._call({}, enforce_delegation_child_decision_fn=_deny, execute=_execute)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "spawn_denied")
        self.assertIn("policy blocked this", result["message"])
        self.assertFalse(create_called)

    def test_timeout_is_an_explicit_refusal_not_a_silent_hang(self):
        clock = _FakeClock()
        store, lookup = self._fake_run_store()

        def _execute(request, *, stamp_request_owner_fn, services, current_user=None):
            store["child-1"] = {"run_id": "child-1", "status": "running"}
            return {"run_id": "child-1"}

        result = self._call(
            {},
            store=store,
            lookup=lookup,
            execute=_execute,
            wait_timeout_seconds=5.0,
            poll_interval_seconds=1.0,
            sleep_fn=clock.sleep,
            monotonic_fn=clock.monotonic,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "timeout")
        self.assertIn("did not finish", result["message"])
        # Creation still counted -- the slot was spent, the run is real and
        # may still be running in the background.
        self.assertEqual(result["spawns_used"], 1)

    def test_orchestrator_role_hint_is_forced_to_builder(self):
        store, lookup = self._fake_run_store()
        captured_requests = []

        def _execute(request, *, stamp_request_owner_fn, services, current_user=None):
            captured_requests.append(request)
            store["child-1"] = {"run_id": "child-1", "status": "completed", "result_summary": "ok"}
            return {"run_id": "child-1"}

        result = self._call({}, store=store, lookup=lookup, execute=_execute, role="orchestrator")
        self.assertTrue(result["ok"])
        self.assertEqual(captured_requests[0].agent_role, "builder")


@pytest.mark.kernel
class SpawnSubagentRealKernelGateTests(unittest.TestCase):
    """End-to-end test driving the real chat-turn seam: the REAL compiled
    Rust run-routing kernel gate (_enforce_delegation_child_decision, no
    mock), the REAL run_service.assert_subagent_spawn_allowed depth check,
    and the REAL run_service.build_delegated_child_run_request lineage
    stamping all execute for real. Only the deepest boundary -- actually
    creating/running a background run (which would otherwise start a real
    LLM agent loop) -- is faked, the same boundary
    test_multi_runtime_demo_proof.py already fakes for the pre-existing
    delegate_run_children path (create_run_from_request), so this test never
    touches a network or a real provider.

    Marked @pytest.mark.kernel: server_modules/tests/conftest.py's autouse
    _skip_kernel_tests_when_binary_missing fixture only lets a kernel-marked
    test through to the REAL compiled binary; every other test in this file
    gets the fixture's own Python mock instead (which does not model
    "run-routing-decision" at all, so this test would otherwise fail with a
    misleading "next_action: missing" against the wrong thing entirely)."""

    def setUp(self) -> None:
        from server_modules import rust_runtime_kernel_client

        if not rust_runtime_kernel_client.runtime_kernel_available():
            self.skipTest("compiled empyralis-runtime-kernel binary not available in this environment")

    def test_real_chat_turn_spawn_drives_the_real_kernel_gate_and_engine(self):
        store: dict[str, dict] = {}

        def _lookup(run_id: str) -> dict:
            return dict(store.get(run_id) or {})

        def _execute(request, *, stamp_request_owner_fn, services, current_user=None):
            run_id = "real-kernel-child-1"
            store[run_id] = {
                "run_id": run_id,
                "status": "completed",
                "result_summary": "Sub-agent finished the assigned research task.",
            }
            return {"run_id": run_id}

        session_ctx: dict = {}
        result = runtime_run_delegation_service.spawn_subagent_from_chat_turn(
            task_description="Research the top 3 competitors and summarize their pricing.",
            role="research",
            session_ctx=session_ctx,
            workspace_id="ws-real-kernel-test",
            tenant_id="default",
            owner_user_id="user-1",
            acting_agent_install_id="agent-pixel",
            # enforce_delegation_child_decision_fn, build_delegated_run_request_fn,
            # normalize_agent_role_fn, assert_subagent_spawn_allowed_fn: all
            # left at their REAL defaults (the real module under test).
            execute_system_run_start_request_via_turn_runtime_fn=_execute,
            run_execution_services_fn=lambda: object(),
            lookup_run_snapshot_fn=_lookup,
            stamp_request_owner_fn=lambda req, current_user: req,
            sleep_fn=lambda seconds: None,
            monotonic_fn=lambda: 0.0,
        )
        self.assertTrue(result["ok"], msg=result)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["summary"], "Sub-agent finished the assigned research task.")
        self.assertEqual(result["spawns_used"], 1)
        self.assertEqual(set(result.keys()), {"ok", "run_id", "status", "summary", "spawns_used", "spawns_remaining"})


if __name__ == "__main__":
    unittest.main()

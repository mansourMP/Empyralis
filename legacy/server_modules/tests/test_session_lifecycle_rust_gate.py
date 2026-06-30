import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from server_modules import session_lifecycle_service


def _old_session() -> dict:
    return {
        "session_id": "session-1",
        "status": "active",
        "created_at": (datetime.now(timezone.utc) - timedelta(days=10)).isoformat().replace("+00:00", "Z"),
        "expires_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
    }


def test_prune_candidates_are_selected_by_rust_lifecycle_decision() -> None:
    async def run() -> None:
        with patch.object(
            session_lifecycle_service.session_service,
            "list_workspace_runtime_sessions",
            new=AsyncMock(return_value=[_old_session()]),
        ), patch.object(
            session_lifecycle_service.rust_runtime_kernel_client,
            "session_lifecycle_decision",
            return_value={"decision": "block", "reason": "session_not_prune_eligible"},
        ) as kernel:
            candidates = await session_lifecycle_service.list_prune_candidates("workspace-1")

        assert candidates == []
        assert kernel.call_args.kwargs["operation"] == "prune"
        assert kernel.call_args.kwargs["session_id"] == "session-1"

    asyncio.run(run())


def test_prune_candidates_wrong_next_action_are_rejected() -> None:
    async def run() -> None:
        with patch.object(
            session_lifecycle_service.session_service,
            "list_workspace_runtime_sessions",
            new=AsyncMock(return_value=[_old_session()]),
        ), patch.object(
            session_lifecycle_service.rust_runtime_kernel_client,
            "session_lifecycle_decision",
            return_value={"decision": "allow", "reason": "session_prune_allowed", "next_action": "continue"},
        ):
            try:
                await session_lifecycle_service.list_prune_candidates("workspace-1")
            except RuntimeError as exc:
                assert str(exc) == "unexpected_next_action:continue"
                return
            raise AssertionError("expected prune candidate selection to fail closed on wrong next_action")

    asyncio.run(run())


def test_prune_idle_sessions_blocks_before_termination_when_rust_denies() -> None:
    async def run() -> None:
        with patch.object(
            session_lifecycle_service,
            "list_prune_candidates",
            new=AsyncMock(return_value=[_old_session()]),
        ), patch.object(
            session_lifecycle_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=RuntimeError("rust denied"),
        ) as kernel, patch.object(
            session_lifecycle_service.session_service,
            "terminate_session",
            new=AsyncMock(),
        ) as terminate:
            result = await session_lifecycle_service.prune_idle_sessions("workspace-1")

        assert result["pruned_count"] == 0
        command, payload = kernel.call_args.args
        assert command == "session-lifecycle-decision"
        assert payload["operation"] == "prune"
        assert payload["session_id"] == "session-1"
        terminate.assert_not_awaited()

    asyncio.run(run())


def test_prune_idle_sessions_wrong_next_action_blocks_before_termination() -> None:
    async def run() -> None:
        with patch.object(
            session_lifecycle_service,
            "list_prune_candidates",
            new=AsyncMock(return_value=[_old_session()]),
        ), patch.object(
            session_lifecycle_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={"ok": True, "decision": "allow", "next_action": "continue"},
        ), patch.object(
            session_lifecycle_service.session_service,
            "terminate_session",
            new=AsyncMock(),
        ) as terminate:
            result = await session_lifecycle_service.prune_idle_sessions("workspace-1")

        assert result["pruned_count"] == 0
        terminate.assert_not_awaited()

    asyncio.run(run())


def test_check_max_turns_wrong_next_action_fails_closed() -> None:
    policy = session_lifecycle_service.resolve_lifecycle_policy(
        overrides={"enforce_max_turns": True, "max_turns_per_session": 3}
    )
    with patch.object(
        session_lifecycle_service.rust_runtime_kernel_client,
        "session_lifecycle_decision",
        return_value={"decision": "allow", "reason": "turn_allowed", "next_action": "prune"},
    ) as kernel:
        allowed = session_lifecycle_service.check_max_turns(1, policy)

    assert allowed is False
    assert kernel.call_args.kwargs["operation"] == "turn"

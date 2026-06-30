from pathlib import Path
from unittest.mock import patch

import pytest

from server_modules import runtime_state_store


def test_runtime_session_write_blocks_before_sqlite_mutation() -> None:
    item = {
        "session_id": "session-1",
        "workspace_id": "workspace-1",
        "tenant_id": "tenant-1",
        "status": "ready",
    }
    with patch.object(
        runtime_state_store.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            runtime_state_store.upsert_runtime_session(Path("/tmp/not-used-runtime-state.sqlite"), item)

    command, payload = kernel.call_args.args
    assert command == "runtime-state-store-decision"
    assert payload["operation"] == "upsert_runtime_session"
    assert payload["state_class"] == "runtime_sessions"
    assert payload["storage_engine"] == "local_sqlite"
    assert payload["session_id"] == "session-1"
    assert payload["local_checkpoint_allowed"] is True


def test_live_run_checkpoint_write_blocks_before_sqlite_mutation() -> None:
    item = {
        "run_id": "run-1",
        "workspace_id": "workspace-1",
        "tenant_id": "tenant-1",
        "status": "running",
    }
    with patch.object(
        runtime_state_store.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            runtime_state_store.upsert_live_run_state(Path("/tmp/not-used-runtime-state.sqlite"), item)

    command, payload = kernel.call_args.args
    assert command == "runtime-state-store-decision"
    assert payload["operation"] == "upsert_live_run"
    assert payload["state_class"] == "live_runs"
    assert payload["storage_engine"] == "local_sqlite"
    assert payload["run_id"] == "run-1"
    assert payload["payload_bytes"] > 0


def test_notification_checkpoint_write_blocks_before_sqlite_mutation() -> None:
    item = {
        "id": "notif-1",
        "workspace_id": "workspace-1",
        "tenant_id": "tenant-1",
        "event_type": "notification",
        "text": "hello",
    }
    with patch.object(
        runtime_state_store.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            runtime_state_store.upsert_notification(Path("/tmp/not-used-runtime-state.sqlite"), item)

    command, payload = kernel.call_args.args
    assert command == "runtime-state-store-decision"
    assert payload["operation"] == "upsert_notification"
    assert payload["state_class"] == "notifications"
    assert payload["storage_engine"] == "local_sqlite"
    assert payload["notification_id"] == "notif-1"


def test_notification_read_blocks_before_sqlite_mutation() -> None:
    with patch.object(
        runtime_state_store.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel, patch.object(
        runtime_state_store,
        "list_notifications",
        return_value=[{"id": "notif-1"}],
    ):
        with pytest.raises(RuntimeError, match="rust denied"):
            runtime_state_store.mark_notifications_read(
                Path("/tmp/not-used-runtime-state.sqlite"),
                reader_key="reader-1",
                mark_all=True,
                workspace_id="workspace-1",
                tenant_id="tenant-1",
            )

    command, payload = kernel.call_args.args
    assert command == "runtime-state-store-decision"
    assert payload["operation"] == "mark_notification_read"
    assert payload["notification_id"] == "notif-1"
    assert payload["actor_id"] == "reader-1"


def test_notification_device_write_blocks_before_sqlite_mutation() -> None:
    item = {
        "device_id": "device-1",
        "workspace_id": "workspace-1",
        "tenant_id": "tenant-1",
        "push_token": "push-token",
        "provider": "expo",
        "status": "active",
    }
    with patch.object(
        runtime_state_store.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            runtime_state_store.upsert_notification_device(Path("/tmp/not-used-runtime-state.sqlite"), item)

    command, payload = kernel.call_args.args
    assert command == "runtime-state-store-decision"
    assert payload["operation"] == "register_notification_device"
    assert payload["state_class"] == "notification_devices"
    assert payload["device_id"] == "device-1"


def test_notification_delivery_write_blocks_before_sqlite_mutation() -> None:
    with patch.object(
        runtime_state_store.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            runtime_state_store.upsert_notification_delivery(
                Path("/tmp/not-used-runtime-state.sqlite"),
                notification_id="notif-1",
                device_id="device-1",
                status="delivered",
            )

    command, payload = kernel.call_args.args
    assert command == "runtime-state-store-decision"
    assert payload["operation"] == "update_notification_delivery"
    assert payload["state_class"] == "notification_delivery_statuses"
    assert payload["notification_id"] == "notif-1"
    assert payload["device_id"] == "device-1"


def test_chat_stream_checkpoint_write_blocks_before_sqlite_mutation() -> None:
    item = {
        "session_id": "session-1",
        "workspace_id": "workspace-1",
        "status": "active",
        "thread_id": "thread-1",
    }
    with patch.object(
        runtime_state_store.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            runtime_state_store.upsert_chat_stream_state(Path("/tmp/not-used-runtime-state.sqlite"), item)

    command, payload = kernel.call_args.args
    assert command == "runtime-state-store-decision"
    assert payload["operation"] == "upsert_chat_stream_state"
    assert payload["state_class"] == "chat_stream_state"
    assert payload["storage_engine"] == "local_sqlite"
    assert payload["session_id"] == "session-1"


def test_channel_event_checkpoint_write_blocks_before_sqlite_mutation() -> None:
    item = {
        "id": "evt-1",
        "workspace_id": "workspace-1",
        "channel": "telegram",
        "direction": "outbound",
        "event_type": "notification",
        "trace_id": "trace-1",
        "run_id": "run-1",
    }
    with patch.object(
        runtime_state_store.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            runtime_state_store.append_channel_event(Path("/tmp/not-used-runtime-state.sqlite"), item, 50)

    command, payload = kernel.call_args.args
    assert command == "runtime-state-store-decision"
    assert payload["operation"] == "append_channel_event"
    assert payload["state_class"] == "channel_events"
    assert payload["storage_engine"] == "local_sqlite"
    assert payload["run_id"] == "run-1"
    assert payload["trace_id"] == "trace-1"


def test_replace_local_runtime_state_blocks_before_sqlite_mutation() -> None:
    with patch.object(
        runtime_state_store.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            runtime_state_store.replace_local_runtime_state(
                Path("/tmp/not-used-runtime-state.sqlite"),
                pending_run_ids=["run-1"],
                claimed_runs={"run-1": {"worker_id": "worker-1"}},
                runtime_registrations={"worker-1": {"status": "idle"}},
            )

    command, payload = kernel.call_args.args
    assert command == "runtime-state-store-decision"
    assert payload["operation"] == "checkpoint_snapshot"
    assert payload["state_class"] == "local_app_state"
    assert payload["storage_engine"] == "local_sqlite"


def test_replace_channel_events_blocks_before_sqlite_mutation() -> None:
    with patch.object(
        runtime_state_store.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            runtime_state_store.replace_channel_events(
                Path("/tmp/not-used-runtime-state.sqlite"),
                items=[
                    {
                        "id": "evt-1",
                        "workspace_id": "workspace-1",
                        "trace_id": "trace-1",
                        "channel": "telegram",
                        "direction": "outbound",
                        "event_type": "notification",
                    }
                ],
                limit=50,
            )

    command, payload = kernel.call_args.args
    assert command == "runtime-state-store-decision"
    assert payload["operation"] == "checkpoint_snapshot"
    assert payload["state_class"] == "channel_events"
    assert payload["trace_id"] == "trace-1"


def test_delete_live_run_state_blocks_before_sqlite_mutation(tmp_path) -> None:
    db_path = tmp_path / "runtime-state.sqlite"
    runtime_state_store.init_runtime_state_db(db_path)
    with runtime_state_store._connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO live_runs (
                run_id, status, engine, workspace_id, execution_target,
                created_at, updated_at, sort_ts, run_json, persisted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-1",
                "failed",
                "orion",
                "workspace-1",
                "",
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                0.0,
                '{"run_id":"run-1","workspace_id":"workspace-1","tenant_id":"tenant-1","status":"failed"}',
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.commit()

    with patch.object(
        runtime_state_store.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            runtime_state_store.delete_live_run_state(db_path, "run-1")

    command, payload = kernel.call_args.args
    assert command == "runtime-state-store-decision"
    assert payload["operation"] == "delete_live_run"
    assert payload["state_class"] == "live_runs"
    assert payload["run_id"] == "run-1"


def test_delete_chat_stream_sessions_older_than_blocks_before_sqlite_mutation(tmp_path) -> None:
    db_path = tmp_path / "runtime-state.sqlite"
    runtime_state_store.init_runtime_state_db(db_path)
    with runtime_state_store._connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO chat_stream_state (
                session_id, thread_id, request_id, workspace_id, status,
                created_at, updated_at, last_event_seq, partial_text,
                final_payload_json, error_text, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "session-1",
                "thread-1",
                "req-1",
                "workspace-1",
                "completed",
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                1,
                "",
                None,
                None,
                "{}",
            ),
        )
        conn.commit()

    with patch.object(
        runtime_state_store.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            runtime_state_store.delete_chat_stream_sessions_older_than(
                db_path,
                older_than_ts=runtime_state_store._parse_ts("2026-06-02T00:00:00Z"),
            )

    command, payload = kernel.call_args.args
    assert command == "runtime-state-store-decision"
    assert payload["operation"] == "prune_records"
    assert payload["state_class"] == "chat_stream_state"
    assert payload["records_requested"] == 1


def test_wrong_rust_action_blocks_live_run_checkpoint_before_sqlite_mutation(tmp_path) -> None:
    db_path = tmp_path / "runtime-state.sqlite"
    runtime_state_store.init_runtime_state_db(db_path)
    item = {
        "run_id": "run-1",
        "workspace_id": "workspace-1",
        "tenant_id": "tenant-1",
        "status": "running",
    }
    with patch.object(
        runtime_state_store.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "operation": "upsert_live_run",
            "next_action": "write_notification",
        },
    ):
        with pytest.raises(RuntimeError, match="unexpected next_action"):
            runtime_state_store.upsert_live_run_state(db_path, item)

    with runtime_state_store._connect(db_path) as conn:
        row = conn.execute("SELECT run_id FROM live_runs WHERE run_id = ?", ("run-1",)).fetchone()
    assert row is None


def test_wrong_rust_action_blocks_prune_before_sqlite_mutation(tmp_path) -> None:
    db_path = tmp_path / "runtime-state.sqlite"
    runtime_state_store.init_runtime_state_db(db_path)
    with runtime_state_store._connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO chat_stream_state (
                session_id, thread_id, request_id, workspace_id, status,
                created_at, updated_at, last_event_seq, partial_text,
                final_payload_json, error_text, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "session-1",
                "thread-1",
                "req-1",
                "workspace-1",
                "completed",
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                1,
                "",
                None,
                None,
                "{}",
            ),
        )
        conn.commit()

    with patch.object(
        runtime_state_store.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "operation": "prune_records",
            "next_action": "write_checkpoint_snapshot",
        },
    ):
        with pytest.raises(RuntimeError, match="unexpected next_action"):
            runtime_state_store.delete_chat_stream_sessions_older_than(
                db_path,
                older_than_ts=runtime_state_store._parse_ts("2026-06-02T00:00:00Z"),
            )

    with runtime_state_store._connect(db_path) as conn:
        row = conn.execute(
            "SELECT session_id FROM chat_stream_state WHERE session_id = ?",
            ("session-1",),
        ).fetchone()
    assert row is not None

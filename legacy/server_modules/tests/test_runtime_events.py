import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from server_modules import runtime_events, runtime_state_store


def _parse_utc_ts(value):
    if value is None:
        return None
    token = str(value).strip()
    if not token:
        return None
    if token.endswith("Z"):
        token = token[:-1] + "+00:00"
    return datetime.fromisoformat(token).astimezone(timezone.utc).timestamp()


class RuntimeEventsStreamTests(unittest.TestCase):
    def _configure_runtime_events(self, db_path: Path) -> None:
        runtime_events.configure_runtime_events(
            channel_events=[],
            channel_events_lock=threading.Lock(),
            channel_events_limit=50,
            channel_sessions_limit=20,
            channel_events_file=db_path.with_suffix(".json"),
            runtime_state_db=str(db_path),
            list_channel_events_fn=runtime_state_store.list_channel_events,
            replace_channel_events_fn=runtime_state_store.replace_channel_events,
            append_channel_event_fn=runtime_state_store.append_channel_event,
            utc_now_iso=lambda: "2026-04-12T00:00:00Z",
            parse_utc_ts=_parse_utc_ts,
            normalize_workspace_id=lambda value: str(value or "default").strip() or "default",
            normalize_tenant_id=lambda value: str(value or "default").strip() or "default",
            compact_event_text=lambda value, limit=800: str(value or "")[:limit],
            json_safe=lambda value: value if isinstance(value, dict) else {},
            safe_read_json=lambda path, fallback: fallback,
        )

    def test_iter_channel_events_stream_replays_bounded_backlog_from_durable_store(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "runtime-state.sqlite3"
            runtime_state_store.init_runtime_state_db(db_path)
            self._configure_runtime_events(db_path)
            for event_id, ts in (
                ("evt-1", "2026-04-08T00:00:00Z"),
                ("evt-2", "2026-04-08T00:01:00Z"),
                ("evt-3", "2026-04-08T00:02:00Z"),
            ):
                runtime_state_store.append_channel_event(
                    db_path,
                    {
                        "id": event_id,
                        "ts": ts,
                        "tenant_id": "tenant-1",
                        "workspace_id": "workspace-1",
                        "channel": "telegram",
                        "direction": "inbound",
                        "event_type": "message",
                        "session_key": "chat-1",
                        "session_id": "chat-1",
                        "trace_id": f"trace-{event_id}",
                        "action": "message_received",
                        "text": event_id,
                        "metadata": {},
                    },
                    50,
                )

            iterator = runtime_events.iter_channel_events_stream(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                include_backlog=True,
                limit=3,
            )
            events = [next(iterator), next(iterator), next(iterator)]
            iterator.close()

            self.assertEqual(
                [json.loads(event["data"])["id"] for event in events],
                ["evt-1", "evt-2", "evt-3"],
            )

    def test_iter_channel_events_stream_resumes_from_since_id_with_durable_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "runtime-state.sqlite3"
            runtime_state_store.init_runtime_state_db(db_path)
            self._configure_runtime_events(db_path)
            for event_id, ts in (
                ("evt-1", "2026-04-08T00:00:00Z"),
                ("evt-2", "2026-04-08T00:01:00Z"),
                ("evt-3", "2026-04-08T00:02:00Z"),
            ):
                runtime_state_store.append_channel_event(
                    db_path,
                    {
                        "id": event_id,
                        "ts": ts,
                        "tenant_id": "tenant-1",
                        "workspace_id": "workspace-1",
                        "channel": "telegram",
                        "direction": "inbound",
                        "event_type": "message",
                        "session_key": "chat-1",
                        "session_id": "chat-1",
                        "trace_id": f"trace-{event_id}",
                        "action": "message_received",
                        "text": event_id,
                        "metadata": {},
                    },
                    50,
                )

            iterator = runtime_events.iter_channel_events_stream(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                since_id="evt-2",
                limit=3,
            )
            event = next(iterator)
            iterator.close()

            self.assertEqual(json.loads(event["data"])["id"], "evt-3")


if __name__ == "__main__":
    unittest.main()

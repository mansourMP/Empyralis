from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import gateway_state_repository


class GatewayStateRepositoryCliLoginEventsTests(unittest.TestCase):
    """Round-trips a real cli.login.output event through the same
    record_gateway_event -> sanitize -> SQLite -> list_gateway_events path
    gateway_protocol_service already uses unconditionally for every inbound
    frame (record_gateway_event is called before any frame_kind branching,
    so cli.login.output needs no new persistence wiring — this proves that
    claim against the real repository + real redaction, not just by reading
    the code). Also proves list_gateway_events' new message_type filter
    works against a real DB, and that the URL / code-prompt text a login
    session forwards survives redaction intact while an unrelated
    credential-shaped field would still be caught."""

    def setUp(self) -> None:
        global gateway_state_repository
        gateway_state_repository = importlib.import_module("server_modules.gateway_state_repository")
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "gateway-state.sqlite3"
        gateway_state_repository.init_gateway_state_db(self.db_path)

        def _rust_side_effect(command: str, payload):
            return {"ok": True, "decision": "allow", "next_action": "record_gateway_event"}

        self.patchers = [
            patch.object(gateway_state_repository, "GATEWAY_STATE_DB_FILE", self.db_path),
            patch.object(
                gateway_state_repository.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                side_effect=_rust_side_effect,
            ),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.tmpdir.cleanup()

    def test_cli_login_output_event_round_trips_with_url_intact(self) -> None:
        gateway_state_repository.record_gateway_event(
            gateway_id="gw-1",
            session_id="sess-1",
            direction="inbound",
            frame_kind="event",
            message_type="cli.login.output",
            payload={
                "type": "cli.login.output",
                "payload": {
                    "run_id": "run-1",
                    "runtime": "claude_code",
                    "event": "output",
                    "kind": "url",
                    "text": "https://claude.ai/oauth/authorize?state=abc123",
                },
            },
        )
        items = gateway_state_repository.list_gateway_events("gw-1", message_type="cli.login.output")
        self.assertEqual(len(items), 1)
        stored_payload = items[0]["payload"]["payload"]
        self.assertEqual(stored_payload["run_id"], "run-1")
        self.assertEqual(stored_payload["text"], "https://claude.ai/oauth/authorize?state=abc123")

    def test_cli_login_output_done_event_round_trips(self) -> None:
        gateway_state_repository.record_gateway_event(
            gateway_id="gw-1",
            session_id="sess-1",
            direction="inbound",
            frame_kind="event",
            message_type="cli.login.output",
            payload={"type": "cli.login.output", "payload": {"run_id": "run-1", "event": "done", "ok": True}},
        )
        items = gateway_state_repository.list_gateway_events("gw-1", message_type="cli.login.output")
        self.assertEqual(items[0]["payload"]["payload"]["ok"], True)

    def test_message_type_filter_excludes_other_event_types_on_the_same_gateway(self) -> None:
        gateway_state_repository.record_gateway_event(
            gateway_id="gw-1", session_id="sess-1", direction="inbound",
            frame_kind="event", message_type="cli.login.output",
            payload={"payload": {"run_id": "run-1", "event": "output"}},
        )
        gateway_state_repository.record_gateway_event(
            gateway_id="gw-1", session_id="sess-1", direction="inbound",
            frame_kind="event", message_type="channel.inbound",
            payload={"payload": {"channel_key": "telegram_personal", "text": "hi"}},
        )
        items = gateway_state_repository.list_gateway_events("gw-1", message_type="cli.login.output")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["message_type"], "cli.login.output")

    def test_a_credential_shaped_field_name_is_still_redacted_in_a_cli_login_payload(self) -> None:
        """Belt-and-suspenders check: even though cli-login-session.ts's
        extractSafeLines never forwards a credential, this proves the shared
        backend redaction path (keyed on field name, e.g. "code") still
        would catch one if it ever appeared, for this message_type too."""
        gateway_state_repository.record_gateway_event(
            gateway_id="gw-1", session_id="sess-1", direction="inbound",
            frame_kind="event", message_type="cli.login.output",
            payload={"payload": {"run_id": "run-1", "event": "output", "code": "should-never-survive"}},
        )
        items = gateway_state_repository.list_gateway_events("gw-1", message_type="cli.login.output")
        self.assertEqual(items[0]["payload"]["payload"]["code"], "[redacted]")


if __name__ == "__main__":
    unittest.main()

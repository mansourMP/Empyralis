from __future__ import annotations

from pathlib import Path

from server_modules import gateway_state_repository


def test_gateway_events_redact_setup_secrets(tmp_path: Path) -> None:
    db_path = tmp_path / "gateway-state.sqlite3"

    gateway_state_repository.record_gateway_event(
        gateway_id="gateway-1",
        session_id="session-1",
        direction="outbound",
        frame_kind="request",
        message_type="tool.invoke",
        payload={
            "payload": {
                "arguments": {
                    "api_hash": "hash-123",
                    "login_code": "12345",
                    "password": "secret-password",
                    "bot_token": "discord-token",
                    "safe": "kept",
                }
            }
        },
        db_path=db_path,
    )

    [event] = gateway_state_repository.list_gateway_events("gateway-1", db_path=db_path)
    arguments = event["payload"]["payload"]["arguments"]

    assert arguments["api_hash"] == "[redacted]"
    assert arguments["login_code"] == "[redacted]"
    assert arguments["password"] == "[redacted]"
    assert arguments["bot_token"] == "[redacted]"
    assert arguments["safe"] == "kept"


def test_gateway_events_redact_personal_channel_text(tmp_path: Path) -> None:
    db_path = tmp_path / "gateway-state.sqlite3"

    gateway_state_repository.record_gateway_event(
        gateway_id="gateway-1",
        session_id="session-1",
        direction="inbound",
        frame_kind="request",
        message_type="channel.inbound",
        payload={
            "payload": {
                "channel_key": "telegram_personal",
                "message": {
                    "text": "private message",
                    "remote_jid": "user-1",
                },
            }
        },
        db_path=db_path,
    )
    gateway_state_repository.record_gateway_event(
        gateway_id="gateway-1",
        session_id="session-1",
        direction="outbound",
        frame_kind="request",
        message_type="channel.outbound",
        payload={
            "payload": {
                "channel_key": "whatsapp_personal",
                "text": "private reply",
                "delta": "private chunk",
                "remote_jid": "user-1",
            }
        },
        db_path=db_path,
    )

    inbound, outbound = gateway_state_repository.list_gateway_events("gateway-1", db_path=db_path)

    assert inbound["payload"]["payload"]["message"]["text"] == "[redacted]"
    assert inbound["payload"]["payload"]["message"]["remote_jid"] == "user-1"
    assert outbound["payload"]["payload"]["text"] == "[redacted]"
    assert outbound["payload"]["payload"]["delta"] == "[redacted]"
    assert outbound["payload"]["payload"]["remote_jid"] == "user-1"


def test_gateway_events_redact_header_cookie_and_phone_values(tmp_path: Path) -> None:
    db_path = tmp_path / "gateway-state.sqlite3"

    gateway_state_repository.record_gateway_event(
        gateway_id="gateway-1",
        session_id="session-1",
        direction="inbound",
        frame_kind="request",
        message_type="gateway.state.update",
        payload={
            "payload": {
                "headers": {
                    "authorization": "Bearer abc.def",
                    "cookie": "sid=abc123",
                },
                "raw_header": "Authorization: Bearer my-token",
                "phone_contact": "+1 415 555 1234",
            }
        },
        db_path=db_path,
    )

    [event] = gateway_state_repository.list_gateway_events("gateway-1", db_path=db_path)
    payload = event["payload"]["payload"]
    assert payload["headers"]["authorization"] == "[redacted]"
    assert payload["headers"]["cookie"] == "[redacted]"
    assert payload["raw_header"] == "Authorization: [redacted]"
    assert payload["phone_contact"] == "[redacted]"

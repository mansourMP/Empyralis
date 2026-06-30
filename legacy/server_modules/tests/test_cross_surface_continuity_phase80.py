from __future__ import annotations

import asyncio
from contextlib import contextmanager
import importlib
import os
import queue
from unittest.mock import patch

import pytest

from server_modules import entitlements_service, runs_history
import server_modules.auth as auth_module
import server_modules.channel_pairing_service as channel_pairing_service_module
import server_modules.control_plane_repository as control_plane_repository_module
import server_modules.db as db_module
import server_modules.jwt_secret as jwt_secret_module


class _Request:
    headers = {}
    client = None


@contextmanager
def _isolated_modules(monkeypatch: pytest.MonkeyPatch, tmp_path):
    global auth_module
    global channel_pairing_service_module
    global control_plane_repository_module
    global db_module
    global jwt_secret_module

    tracked_keys = (
        "EMPYRALIS_STATE_HOME",
        "EMPYRALIS_JWT_SECRET_FILE",
        "DATABASE_URL",
        "ORION_JWT_SECRET",
        "JWT_SECRET",
        "ORION_API_KEY",
        "RUNTIME_KEY",
    )
    original_env = {key: os.environ.get(key) for key in tracked_keys}
    state_home = tmp_path / "state"
    monkeypatch.setenv("EMPYRALIS_STATE_HOME", str(state_home))
    monkeypatch.delenv("EMPYRALIS_JWT_SECRET_FILE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for key in ("ORION_JWT_SECRET", "JWT_SECRET", "ORION_API_KEY", "RUNTIME_KEY"):
        monkeypatch.delenv(key, raising=False)
    db_module = importlib.import_module("server_modules.db")
    control_plane_repository_module = importlib.import_module("server_modules.control_plane_repository")
    jwt_secret_module = importlib.import_module("server_modules.jwt_secret")
    auth_module = importlib.import_module("server_modules.auth")
    channel_pairing_service_module = importlib.import_module("server_modules.channel_pairing_service")
    runtime_db = importlib.reload(db_module)
    runtime_db._POOLS_BY_LOOP.clear()
    runtime_db._ENV_DSN_LOADED = True
    importlib.reload(control_plane_repository_module)
    importlib.reload(jwt_secret_module)
    auth = importlib.reload(auth_module)
    pairing = importlib.reload(channel_pairing_service_module)
    auth.USER_RATE_LIMIT_BUCKETS.clear()
    auth.LOGIN_RATE_LIMIT_BUCKETS.clear()
    try:
        yield auth, pairing
    finally:
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        db_module = importlib.import_module("server_modules.db")
        control_plane_repository_module = importlib.import_module("server_modules.control_plane_repository")
        jwt_secret_module = importlib.import_module("server_modules.jwt_secret")
        auth_module = importlib.import_module("server_modules.auth")
        channel_pairing_service_module = importlib.import_module("server_modules.channel_pairing_service")
        runtime_db = importlib.reload(db_module)
        runtime_db._POOLS_BY_LOOP.clear()
        importlib.reload(jwt_secret_module)
        importlib.reload(control_plane_repository_module)
        importlib.reload(auth_module)
        importlib.reload(channel_pairing_service_module)


def _current_user(auth, token: str):
    return auth.get_current_user(_Request(), authorization=f"Bearer {token}")


def test_phase80_paired_telegram_and_whatsapp_messages_share_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path):
    with _isolated_modules(monkeypatch, tmp_path) as (auth, pairing_module):
        created = auth.register_user("phase80@example.com", "password-123", name="Phase 80")
        current_user = _current_user(auth, created["token"])
        workspace_id = created["current_workspace_id"]
        service = pairing_module.get_channel_pairing_service()

        unpaired = service.authorize_channel_message(
            provider="telegram",
            external_subject="telegram-user-80",
            workspace_id=workspace_id,
            message_text="hello before pairing",
        )
        assert unpaired["authorized"] is False
        assert unpaired["status"] == "pairing_required"
        assert unpaired["connect_url"].endswith(
            f"/continue?source=channel_connect&channel=telegram&workspace_id={workspace_id}"
        )
        assert "/pair" not in unpaired["reply_text"].lower()

        telegram_intent = pairing_module.create_authenticated_channel_pairing_intent(
            current_user,
            provider="telegram",
            workspace_id="default",
        )
        whatsapp_intent = pairing_module.create_authenticated_channel_pairing_intent(
            current_user,
            provider="whatsapp",
            workspace_id="default",
        )

        telegram_pair = service.authorize_channel_message(
            provider="telegram",
            external_subject="telegram-user-80",
            workspace_id=workspace_id,
            message_text=f"/pair {telegram_intent['intent']['pairing_code']}",
        )
        whatsapp_pair = service.authorize_channel_message(
            provider="whatsapp",
            external_subject="whatsapp:+15558000080",
            workspace_id=workspace_id,
            message_text=f"pair {whatsapp_intent['intent']['pairing_code']}",
        )

        assert telegram_pair["status"] == "paired"
        assert whatsapp_pair["status"] == "paired"

        telegram_message = service.authorize_channel_message(
            provider="telegram",
            external_subject="telegram-user-80",
            workspace_id=workspace_id,
            message_text="hello from telegram",
        )
        whatsapp_message = service.authorize_channel_message(
            provider="whatsapp",
            external_subject="whatsapp:+15558000080",
            workspace_id=workspace_id,
            message_text="hello from whatsapp",
        )

        assert telegram_message["authorized"] is True
        assert whatsapp_message["authorized"] is True
        assert telegram_message["workspace_id"] == workspace_id
        assert whatsapp_message["workspace_id"] == workspace_id
        assert telegram_message["link"]["account_id"] == current_user["user_id"]
        assert whatsapp_message["link"]["account_id"] == current_user["user_id"]


def test_phase80_channel_identity_cannot_cross_workspace_boundary(monkeypatch: pytest.MonkeyPatch, tmp_path):
    with _isolated_modules(monkeypatch, tmp_path) as (auth, pairing_module):
        user_one = auth.register_user("phase80.one@example.com", "password-123", name="Phase One")
        user_two = auth.register_user("phase80.two@example.com", "password-123", name="Phase Two")
        current_user_one = _current_user(auth, user_one["token"])
        current_user_two = _current_user(auth, user_two["token"])
        workspace_one = user_one["current_workspace_id"]
        workspace_two = user_two["current_workspace_id"]
        service = pairing_module.get_channel_pairing_service()

        first_intent = pairing_module.create_authenticated_channel_pairing_intent(
            current_user_one,
            provider="telegram",
            workspace_id="default",
        )
        paired = service.authorize_channel_message(
            provider="telegram",
            external_subject="telegram-shared-user",
            workspace_id=workspace_one,
            message_text=f"/pair {first_intent['intent']['pairing_code']}",
        )
        assert paired["status"] == "paired"

        second_intent = pairing_module.create_authenticated_channel_pairing_intent(
            current_user_two,
            provider="telegram",
            workspace_id="default",
        )
        blocked = service.authorize_channel_message(
            provider="telegram",
            external_subject="telegram-shared-user",
            workspace_id=workspace_two,
            message_text=f"/pair {second_intent['intent']['pairing_code']}",
        )

        assert blocked["authorized"] is False
        assert blocked["status"] == "pairing_failed"
        assert "already linked" in blocked["reply_text"].lower()


def test_phase80_paid_mobile_and_free_channel_boundaries():
    free_flags = entitlements_service.workspace_capability_flags(
        state=entitlements_service.resolve_workspace_entitlement_state(
            workspace={"metadata": {"billing": {"plan": "free"}}},
        )
    )
    paid_flags = entitlements_service.workspace_capability_flags(
        state=entitlements_service.resolve_workspace_entitlement_state(
            workspace={"metadata": {"billing": {"plan": "personal"}}},
        )
    )

    assert free_flags["mobile_app_enabled"] is True
    assert free_flags["mobile_push_enabled"] is False
    assert free_flags["approvals_enabled"] is False
    assert free_flags["telegram_channel_enabled"] is True
    assert free_flags["whatsapp_channel_enabled"] is True
    assert paid_flags["mobile_app_enabled"] is True
    assert paid_flags["approvals_enabled"] is True
    assert paid_flags["artifacts_enabled"] is True



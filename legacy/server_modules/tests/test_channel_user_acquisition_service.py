from __future__ import annotations

from contextlib import contextmanager
import sqlite3
from urllib.parse import parse_qs, urlsplit

from server_modules.channel_user_acquisition_service import (
    CHANNEL_ATTRIBUTION_QUERY_PARAM,
    ChannelUserAcquisitionService,
)


@contextmanager
def _service():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row

    def _control_plane_runner(coro):
        try:
            coro.close()
        except Exception:
            pass
        return None

    try:
        yield ChannelUserAcquisitionService(
            now_epoch=lambda: 1_710_000_000,
            jwt_secret_resolver=lambda: "test-secret",
            control_plane_runner=_control_plane_runner,
            frontend_signup_url_resolver=lambda: "https://app.empyralist.com/signup",
            fallback_connection_factory=lambda: connection,
        )
    finally:
        connection.close()


@contextmanager
def _service_with_workspace_defaults(defaults: dict[str, object]):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row

    def _control_plane_runner(coro):
        try:
            coro.close()
        except Exception:
            pass
        return {
            "id": "ws-1",
            "metadata": {
                "admin_defaults": {
                    "payload": defaults,
                }
            },
        }

    try:
        yield ChannelUserAcquisitionService(
            now_epoch=lambda: 1_710_000_000,
            jwt_secret_resolver=lambda: "test-secret",
            control_plane_runner=_control_plane_runner,
            frontend_signup_url_resolver=lambda: "https://app.empyralist.com/signup",
            fallback_connection_factory=lambda: connection,
        )
    finally:
        connection.close()


def test_prepare_public_start_response_persists_touch_and_generates_signed_cta() -> None:
    with _service() as service:
        response = service.prepare_public_start_response(
            profile={
                "public_start": {
                    "enabled": True,
                    "tenant_id": "tenant-1",
                    "workspace_id": "ws-1",
                    "deployed_agent_id": "dagent-1",
                    "deployed_agent_name": "HealthGuide",
                    "intro": "HealthGuide gives fast, cautious health information on Telegram.",
                    "cta_label": "Continue on Empyralist",
                    "source": "telegram_start",
                }
            },
            workspace_id="ws-1",
            external_user_id="telegram-user-1",
            sender_username="alice",
            sender_display_name="Alice",
            campaign_token="youtube-health",
        )

        query = parse_qs(urlsplit(response["cta_url"]).query)
        token = query[CHANNEL_ATTRIBUTION_QUERY_PARAM][0]
        touch = service.touch_from_attribution_token(token)

    assert "HealthGuide" in response["text"]
    assert response["cta_label"] == "Continue on Empyralist"
    assert touch is not None
    assert touch["deployed_agent_id"] == "dagent-1"
    assert touch["external_user_id"] == "telegram-user-1"
    assert touch["campaign_token"] == "youtube-health"
    assert touch["start_count"] == 1


def test_prepare_public_start_response_deduplicates_same_external_user() -> None:
    with _service() as service:
        profile = {
            "public_start": {
                "enabled": True,
                "tenant_id": "tenant-1",
                "workspace_id": "ws-1",
                "deployed_agent_id": "dagent-1",
                "deployed_agent_name": "Returns Concierge",
            }
        }

        first = service.prepare_public_start_response(
            profile=profile,
            workspace_id="ws-1",
            external_user_id="telegram-user-1",
        )
        second = service.prepare_public_start_response(
            profile=profile,
            workspace_id="ws-1",
            external_user_id="telegram-user-1",
            campaign_token="spring-campaign",
        )

    assert first["touch"]["id"] == second["touch"]["id"]
    assert second["touch"]["start_count"] == 2
    assert second["touch"]["campaign_token"] == "spring-campaign"


def test_bind_attribution_token_to_user_marks_conversion() -> None:
    with _service() as service:
        response = service.prepare_public_start_response(
            profile={
                "public_start": {
                    "enabled": True,
                    "tenant_id": "tenant-1",
                    "workspace_id": "ws-1",
                    "deployed_agent_id": "dagent-1",
                }
            },
            workspace_id="ws-1",
            external_user_id="telegram-user-9",
        )
        token = parse_qs(urlsplit(response["cta_url"]).query)[CHANNEL_ATTRIBUTION_QUERY_PARAM][0]

        bound = service.bind_attribution_token_to_user(
            attribution_token=token,
            user_id="user-1",
            email="owner@example.com",
            auth_flow="register",
        )

    assert bound is not None
    assert bound["converted_user_id"] == "user-1"
    assert bound["converted_email"] == "owner@example.com"
    assert bound["converted_auth_flow"] == "register"


def test_prepare_public_start_response_uses_workspace_admin_defaults_for_cta() -> None:
    with _service_with_workspace_defaults(
        {
            "public_start_cta_label": "Join the platform",
            "public_start_cta_url": "https://workspace.example.com/start",
        }
    ) as service:
        response = service.prepare_public_start_response(
            profile={
                "public_start": {
                    "enabled": True,
                    "tenant_id": "tenant-1",
                    "workspace_id": "ws-1",
                    "deployed_agent_id": "dagent-1",
                    "deployed_agent_name": "HealthGuide",
                }
            },
            workspace_id="ws-1",
            external_user_id="telegram-user-1",
        )

    assert response["cta_label"] == "Join the platform"
    assert response["cta_url"].startswith("https://workspace.example.com/start?")


def test_start_payload_requires_explicit_slash_command() -> None:
    with _service() as service:
        assert service.start_payload("/start campaign-1") == "campaign-1"
        assert service.start_payload("Start fresh after delete") is None

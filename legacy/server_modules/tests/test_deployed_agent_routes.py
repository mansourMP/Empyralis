from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from server_modules import routes_deployed_agents
from server_modules import deployed_agent_service
from server_modules import run_state_repository
from server_modules import shop_assistant_revenue_agent_service
from server_modules import activity_ledger_service
from server_modules import control_plane_repository


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_deployed_agents.router)
    return app


@pytest.fixture(autouse=True)
def _stub_deployed_agent_route_rust_gate(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        routes_deployed_agents,
        "_enforce_deployed_agent_route_decision",
        lambda **kwargs: {"next_action": "stub"},
    )


def _owner_user() -> dict[str, object]:
    return {
        "user_id": "user-owner",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-1": {
                "workspace_id": "ws-1",
                "tenant_id": "tenant-1",
                "role": "owner",
            }
        },
    }


def _auth_admin_user() -> dict[str, object]:
    return {
        "user_id": "user-admin",
        "auth_type": "bearer",
        "auth_admin": True,
        "workspace_access": {
            "ws-1": {
                "workspace_id": "ws-1",
                "tenant_id": "tenant-1",
                "role": "member",
            }
        },
    }


def _member_user() -> dict[str, object]:
    return {
        "user_id": "user-member",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-1": {
                "workspace_id": "ws-1",
                "tenant_id": "tenant-1",
                "role": "member",
            }
        },
    }


def _created_payload() -> dict[str, object]:
    return {
        "id": "dagent_1",
        "owner_workspace_id": "ws-1",
        "tenant_id": "tenant-1",
        "backing_install_id": "ainstall_1",
        "name": "Store Assistant",
        "avatar": "https://example.com/avatar.png",
        "persona": "Helpful retail agent",
        "system_prompt": "Use the catalog and return policy.",
        "deployment_state": "draft",
        "channels": {"telegram": {"enabled": True, "endpoint_key": "store-bot"}},
        "knowledge_sources": [{"id": "doc-1", "uri": "kb://doc-1"}],
        "runtime_target": "cloud",
        "billing_plan": "free",
        "provider": "openai",
        "model": "gpt-4o",
        "metadata": {
            "daily_message_limit": 25,
            "monthly_cost_cap_usd": 25.0,
            "upgrade_cta_url": "https://app.empyralist.com/signup",
            "upgrade_cta_label": "Continue on Empyralist",
            "provider": "openai",
            "model": "gpt-4o",
        },
        "created_at": "2026-04-13T10:00:00Z",
        "updated_at": "2026-04-13T10:00:00Z",
    }


def _conversation_list_payload() -> dict[str, object]:
    return {
        "deployed_agent_id": "dagent_1",
        "items": [
            {
                "session_id": "sess-1",
                "channel": "telegram",
                "last_message": "Can I return this order?",
                "last_message_at": "2026-04-13T12:05:00Z",
                "customer": {"id": "customer-1", "label": "Customer 1", "type": "customer"},
                "latest_run_id": "run-1",
                "escalation_state": "approval_requested",
                "outcome": "completed",
            }
        ],
        "offset": 0,
        "limit": 50,
        "has_more": False,
    }


def _conversation_detail_payload() -> dict[str, object]:
    return {
        "deployed_agent_id": "dagent_1",
        "session_id": "sess-1",
        "channel": "telegram",
        "thread_id": "thread-1",
        "run_ids": ["run-1"],
        "messages": [
            {
                "id": "cevt-1",
                "kind": "message",
                "direction": "inbound",
                "text": "Can I return this order?",
            },
            {
                "id": "cevt-2",
                "kind": "message",
                "direction": "outbound",
                "text": "Let me check the return policy.",
            },
        ],
        "tool_calls": [{"id": "tool-1", "kind": "tool_call", "tool_name": "telegram_bot.send_message"}],
        "approval_events": [{"id": "approval-evt-1", "kind": "approval", "approval_id": "approval-1"}],
        "escalation_events": [{"id": "esc-1", "kind": "escalation"}],
        "entries": [{"id": "cevt-1"}, {"id": "tool-1"}],
        "outcome": "completed",
    }


def _analytics_payload() -> dict[str, object]:
    return {
        "deployed_agent_id": "dagent_1",
        "active_users_last_30d": 12,
        "message_volume": {
            "day": 3,
            "week": 18,
            "month": 58,
            "latest_message_at": "2026-04-13T12:05:00Z",
        },
        "escalation": {
            "total_sessions": 3,
            "escalated_sessions": 1,
            "rate": 0.3333,
            "rate_percent": 33.33,
        },
        "outcomes": {
            "counts": {
                "completed": 2,
                "open": 1,
            },
            "top_outcome": "completed",
        },
        "cost_burn": {
            "usage_month": "2026-04-01",
            "current_burn_usd": 8.0,
            "cap_usd": 25.0,
            "percent_used": 32.0,
        },
    }


def _admin_dashboard_payload() -> dict[str, object]:
    return {
        "deployed_agent_id": "dagent_1",
        "total_users": 12,
        "messages_today": 4,
        "messages_this_calendar_month": 58,
        "orders_today": 2,
        "revenue_today_usd": 42.5,
        "users_at_limit_today": 0,
        "upgrade_clicks_this_month": 1,
        "common_questions": [{"question": "Do you have oat milk?", "count": 2}],
        "customer_entry": {
            "entry_url": "https://menu.example.com/bluebird",
            "cta_label": "Open menu",
            "telegram_deep_link": "https://t.me/bluebirdcafe_bot?start=dagent_1",
            "bot_username": "bluebirdcafe_bot",
            "qr_image_url": "https://api.qrserver.com/v1/create-qr-code/?size=220x220&data=foo",
            "qr_target": "web",
        },
        "specialist_profile": {
            "knowledge": {"mode": "menu_reference"},
            "live_data": {"mode": "daily_specials_and_availability"},
            "memory": {"mode": "per_customer_order_history"},
            "actions": {"enabled": ["place_order", "confirm_order", "escalate_to_human"]},
            "channel": {"primary": "telegram_bot"},
        },
        "user_rows": [],
        "limit": 50,
        "offset": 0,
        "has_more": False,
    }


def _telegram_readiness_payload() -> dict[str, object]:
    return {
        "channel": "telegram",
        "workspace_id": "ws-1",
        "ready_for_live": True,
        "status": "ready",
        "blockers": [],
        "warnings": [],
        "next_action": "Telegram is ready.",
        "configured_binding": {
            "enabled": True,
            "connector_id": "tg-1",
            "endpoint_key": "store-bot",
            "is_inbound_owner": True,
        },
        "connectors": [
            {
                "id": "tg-1",
                "label": "Store Bot",
                "endpoint_key": "store-bot",
                "webhook_path": "/channels/telegram/webhook/tg-1",
                "webhook_url": "https://runtime.example/channels/telegram/webhook/tg-1",
            }
        ],
        "webhook": {"status": "live", "delivery_mode": "webhook"},
        "autopilot": {"status": "live"},
        "whatsapp": {"available": False, "status": "out_of_scope"},
    }


def _business_insights_payload() -> dict[str, object]:
    return {
        "workspace_id": "ws-1",
        "deployed_agent_id": "dagent_1",
        "count": 1,
        "items": [
            {
                "id": "bins_1",
                "pattern_key": "pricing.price_match_or_discount_pressure",
                "insight_type": "pricing_intelligence",
                "status": "candidate",
                "sensitivity": "orange",
                "event_count": 4,
                "redacted_examples": ["Can you price match this?"],
            }
        ],
    }


def _route_request(case: str) -> dict[str, object]:
    if case == "create":
        return {
            "method": "POST",
            "url": "/deployed-agents",
            "kwargs": {
                "json": {
                    "workspace_id": "ws-1",
                    "name": "Store Assistant",
                    "avatar": "https://example.com/avatar.png",
                    "persona": "Helpful retail agent",
                    "system_prompt": "Use the catalog and return policy.",
                    "channels": {"telegram": {"enabled": True, "endpoint_key": "store-bot"}},
                    "knowledge_sources": [{"id": "doc-1", "uri": "kb://doc-1"}],
                    "metadata": {
                        "daily_message_limit": 25,
                        "monthly_cost_cap_usd": 25.0,
                        "upgrade_cta_url": "https://app.empyralist.com/signup",
                        "upgrade_cta_label": "Continue on Empyralist",
                    },
                    "provider": "openai",
                    "model": "gpt-4o",
                }
            },
        }
    if case == "list":
        return {
            "method": "GET",
            "url": "/deployed-agents",
            "kwargs": {
                "params": {
                    "workspace_id": "ws-1",
                    "deployment_state": "draft",
                }
            },
        }
    if case == "get":
        return {
            "method": "GET",
            "url": "/deployed-agents/dagent_1",
            "kwargs": {"params": {"workspace_id": "ws-1"}},
        }
    if case == "patch":
        return {
            "method": "PATCH",
            "url": "/deployed-agents/dagent_1",
            "kwargs": {
                "json": {
                    "workspace_id": "ws-1",
                    "name": "Store Concierge",
                    "metadata": {
                        "daily_message_limit": 30,
                        "monthly_cost_cap_usd": 30.0,
                        "upgrade_cta_url": "https://app.empyralist.com/upgrade",
                        "upgrade_cta_label": "Unlock more messages",
                    },
                    "provider": "anthropic",
                    "model": "claude-3-5-sonnet-20241022",
                }
            },
        }
    if case == "deploy":
        return {
            "method": "POST",
            "url": "/deployed-agents/dagent_1/deploy",
            "kwargs": {"json": {"workspace_id": "ws-1"}},
        }
    if case == "pause":
        return {
            "method": "POST",
            "url": "/deployed-agents/dagent_1/pause",
            "kwargs": {"json": {"workspace_id": "ws-1"}},
        }
    if case == "delete-user":
        return {
            "method": "POST",
            "url": "/deployed-agents/dagent_1/external-users/customer-1/delete",
            "kwargs": {"json": {"workspace_id": "ws-1", "channel": "telegram", "session_id": "sess-1"}},
        }
    raise AssertionError(f"Unknown route case: {case}")


def _route_success_payload(case: str) -> dict[str, object]:
    if case == "list":
        return {"items": [_created_payload()]}
    if case == "deploy":
        payload = _created_payload()
        payload["deployment_state"] = "live"
        return payload
    if case == "pause":
        payload = _created_payload()
        payload["deployment_state"] = "paused"
        return payload
    if case == "delete-user":
        return {
            "deployed_agent_id": "dagent_1",
            "channel": "telegram",
            "external_user_id": "customer-1",
            "deleted_counts": {
                "deleted_channel_event_count": 4,
                "deleted_memory_count": 1,
                "deleted_daily_usage_count": 2,
                "deleted_acquisition_touch_count": 1,
            },
            "request": {"id": "privreq_1", "status": "completed"},
            "audit": {"id": "privaudit_1"},
        }
    if case == "patch":
        payload = _created_payload()
        payload["name"] = "Store Concierge"
        payload["provider"] = "anthropic"
        payload["model"] = "claude-3-5-sonnet-20241022"
        payload["metadata"] = {
            **dict(payload.get("metadata") or {}),
            "provider": "anthropic",
            "model": "claude-3-5-sonnet-20241022",
        }
        return payload
    return _created_payload()


def _assert_management_service_kwargs(case: str, kwargs: dict[str, object], *, expected_user_id: str, expect_auth_admin: bool) -> None:
    current_user = kwargs.get("current_user")
    assert isinstance(current_user, dict)
    assert current_user["user_id"] == expected_user_id
    assert bool(current_user.get("auth_admin")) is expect_auth_admin
    assert kwargs["owner_workspace_id"] == "ws-1"
    if case in {"get", "patch", "deploy", "pause"}:
        assert kwargs["deployed_agent_id"] == "dagent_1"
    if case == "delete-user":
        assert kwargs["deployed_agent_id"] == "dagent_1"
        assert kwargs["external_user_id"] == "customer-1"
        assert kwargs["channel_key"] == "telegram"
        assert kwargs["session_id"] == "sess-1"
    if case == "create":
        assert kwargs["name"] == "Store Assistant"
        assert kwargs["metadata"]["daily_message_limit"] == 25
        assert kwargs["metadata"]["monthly_cost_cap_usd"] == 25.0
        assert kwargs["provider"] == "openai"
        assert kwargs["model"] == "gpt-4o"
    if case == "patch":
        updates = kwargs.get("updates")
        assert isinstance(updates, dict)
        assert updates["name"] == "Store Concierge"
        assert updates["metadata"]["daily_message_limit"] == 30
        assert updates["metadata"]["monthly_cost_cap_usd"] == 30.0
        assert updates["provider"] == "anthropic"
        assert updates["model"] == "claude-3-5-sonnet-20241022"
    if case == "list":
        assert kwargs["deployment_state"] == "draft"


async def _invoke_management_route(client: httpx.AsyncClient, case: str) -> httpx.Response:
    request = _route_request(case)
    method = str(request["method"]).lower()
    http_method = getattr(client, method)
    return await http_method(str(request["url"]), **dict(request["kwargs"]))


@pytest.mark.anyio
async def test_create_deployed_agent_route_returns_created_payload(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = lambda: {"user_id": "user-1"}

    async def fake_create_draft_deployed_agent(**kwargs):
        assert kwargs["owner_workspace_id"] == "ws-1"
        assert kwargs["name"] == "Store Assistant"
        assert kwargs["provider"] == "openai"
        assert kwargs["model"] == "gpt-4o"
        return _created_payload()

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "create_draft_deployed_agent",
        fake_create_draft_deployed_agent,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/deployed-agents",
            json={
                "workspace_id": "ws-1",
                "name": "Store Assistant",
                "avatar": "https://example.com/avatar.png",
                "persona": "Helpful retail agent",
                "system_prompt": "Use the catalog and return policy.",
                "channels": {"telegram": {"enabled": True, "endpoint_key": "store-bot"}},
                "knowledge_sources": [{"id": "doc-1", "uri": "kb://doc-1"}],
                "provider": "openai",
                "model": "gpt-4o",
            },
        )

    assert response.status_code == 200
    assert response.json()["backing_install_id"] == "ainstall_1"


@pytest.mark.anyio
async def test_create_deployed_agent_route_passes_typed_config(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = lambda: {"user_id": "user-1"}

    async def fake_create_draft_deployed_agent(**kwargs):
        config = kwargs["config"]
        assert config["customer_policy"]["paused_message"] == "Please come back later."
        assert config["memory_policy"]["context_budget_preset"] == "extended"
        assert config["safety_policy"]["enabled"] is True
        assert config["escalation_policy"]["handoff_mode"] == "manual_resume"
        return _created_payload()

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "create_draft_deployed_agent",
        fake_create_draft_deployed_agent,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/deployed-agents",
            json={
                "workspace_id": "ws-1",
                "name": "Store Assistant",
                "provider": "openai",
                "model": "gpt-4o",
                "config": {
                    "customer_policy": {
                        "paused_message": "Please come back later.",
                    },
                    "memory_policy": {
                        "context_budget_preset": "deep",
                    },
                    "safety_policy": {
                        "enabled": True,
                    },
                    "escalation_policy": {
                        "handoff_mode": "manual_resume",
                    },
                },
            },
        )

    assert response.status_code == 200


@pytest.mark.anyio
async def test_get_deployed_agent_telegram_readiness_route_returns_payload(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = _owner_user

    async def fake_readiness(**kwargs):
        assert kwargs["owner_workspace_id"] == "ws-1"
        assert kwargs["deployed_agent_id"] == "dagent_1"
        current_user = kwargs["current_user"]
        assert isinstance(current_user, dict)
        assert current_user["user_id"] == "user-owner"
        return _telegram_readiness_payload()

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "get_deployed_agent_telegram_readiness",
        fake_readiness,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/deployed-agents/telegram-readiness",
            params={"workspace_id": "ws-1", "deployed_agent_id": "dagent_1"},
        )

    assert response.status_code == 200
    assert response.json()["ready_for_live"] is True


@pytest.mark.anyio
async def test_patch_deployed_agent_route_passes_typed_config(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = lambda: {"user_id": "user-1"}

    async def fake_update_deployed_agent(**kwargs):
        updates = kwargs["updates"]
        config = updates["config"]
        assert config["customer_policy"]["public_start_cta_label"] == "Join now"
        assert config["memory_policy"]["retention_preset"] == "extended"
        assert config["escalation_policy"]["owner_notification_destination"] == "ops@example.com"
        return _route_success_payload("patch")

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "update_deployed_agent",
        fake_update_deployed_agent,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.patch(
            "/deployed-agents/dagent_1",
            json={
                "workspace_id": "ws-1",
                "config": {
                    "customer_policy": {
                        "public_start_cta_label": "Join now",
                    },
                    "memory_policy": {
                        "retention_preset": "extended",
                    },
                    "escalation_policy": {
                        "owner_notification_destination": "ops@example.com",
                    },
                },
            },
        )

    assert response.status_code == 200


@pytest.mark.anyio
@pytest.mark.parametrize("case", ["create", "list", "get", "patch", "deploy", "pause", "delete-user"])
@pytest.mark.parametrize(
    ("current_user", "expected_user_id", "expect_auth_admin"),
    [
        pytest.param(_owner_user(), "user-owner", False, id="owner"),
        pytest.param(_auth_admin_user(), "user-admin", True, id="auth-admin"),
    ],
)
async def test_management_routes_allow_owner_and_auth_admin(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    current_user: dict[str, object],
    expected_user_id: str,
    expect_auth_admin: bool,
):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = lambda: current_user
    payload = _route_success_payload(case)

    if case == "create":
        async def fake_service(**kwargs):
            _assert_management_service_kwargs(case, kwargs, expected_user_id=expected_user_id, expect_auth_admin=expect_auth_admin)
            return payload

        monkeypatch.setattr(routes_deployed_agents.deployed_agent_service, "create_draft_deployed_agent", fake_service)
    elif case == "list":
        async def fake_service(**kwargs):
            _assert_management_service_kwargs(case, kwargs, expected_user_id=expected_user_id, expect_auth_admin=expect_auth_admin)
            return payload

        monkeypatch.setattr(routes_deployed_agents.deployed_agent_service, "list_deployed_agents", fake_service)
    elif case == "get":
        async def fake_service(**kwargs):
            _assert_management_service_kwargs(case, kwargs, expected_user_id=expected_user_id, expect_auth_admin=expect_auth_admin)
            return payload

        monkeypatch.setattr(routes_deployed_agents.deployed_agent_service, "get_deployed_agent_detail", fake_service)
    elif case == "patch":
        async def fake_service(**kwargs):
            _assert_management_service_kwargs(case, kwargs, expected_user_id=expected_user_id, expect_auth_admin=expect_auth_admin)
            return payload

        monkeypatch.setattr(routes_deployed_agents.deployed_agent_service, "update_deployed_agent", fake_service)
    elif case == "deploy":
        async def fake_service(**kwargs):
            _assert_management_service_kwargs(case, kwargs, expected_user_id=expected_user_id, expect_auth_admin=expect_auth_admin)
            return payload

        monkeypatch.setattr(routes_deployed_agents.deployed_agent_service, "deploy_deployed_agent", fake_service)
    elif case == "delete-user":
        async def fake_service(**kwargs):
            _assert_management_service_kwargs(case, kwargs, expected_user_id=expected_user_id, expect_auth_admin=expect_auth_admin)
            return payload

        monkeypatch.setattr(routes_deployed_agents.deployed_agent_service, "delete_deployed_agent_external_user_data", fake_service)
        monkeypatch.setattr(
            routes_deployed_agents,
            "_enforce_deployed_agent_route_decision",
            lambda **_kwargs: {"next_action": "delete_deployed_agent_external_user_data"},
        )
    else:
        async def fake_service(**kwargs):
            _assert_management_service_kwargs(case, kwargs, expected_user_id=expected_user_id, expect_auth_admin=expect_auth_admin)
            return payload

        monkeypatch.setattr(routes_deployed_agents.deployed_agent_service, "pause_deployed_agent", fake_service)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await _invoke_management_route(client, case)

    assert response.status_code == 200
    body = response.json()
    if case == "list":
        assert body["items"][0]["id"] == "dagent_1"
    elif case == "patch":
        assert body["name"] == "Store Concierge"
    elif case == "deploy":
        assert body["deployment_state"] == "live"
    elif case == "pause":
        assert body["deployment_state"] == "paused"
    elif case == "delete-user":
        assert body["deleted_counts"]["deleted_channel_event_count"] == 4
    else:
        assert body["id"] == "dagent_1"


@pytest.mark.anyio
@pytest.mark.parametrize("case", ["create", "list", "get", "patch", "deploy", "pause", "delete-user"])
async def test_management_routes_forbid_member_access_consistently(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = _member_user

    async def fake_forbidden_service(**kwargs):
        current_user = kwargs.get("current_user")
        assert isinstance(current_user, dict)
        assert current_user["user_id"] == "user-member"
        assert kwargs["owner_workspace_id"] == "ws-1"
        raise HTTPException(status_code=403, detail="Owner role required for workspace 'ws-1'.")

    service_attr = {
        "create": "create_draft_deployed_agent",
        "list": "list_deployed_agents",
        "get": "get_deployed_agent_detail",
        "patch": "update_deployed_agent",
        "deploy": "deploy_deployed_agent",
        "pause": "pause_deployed_agent",
        "delete-user": "delete_deployed_agent_external_user_data",
    }[case]
    monkeypatch.setattr(routes_deployed_agents.deployed_agent_service, service_attr, fake_forbidden_service)
    if case == "delete-user":
        monkeypatch.setattr(
            routes_deployed_agents,
            "_enforce_deployed_agent_route_decision",
            lambda **_kwargs: {"next_action": "delete_deployed_agent_external_user_data"},
        )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await _invoke_management_route(client, case)

    assert response.status_code == 403
    assert "owner role required" in response.json()["detail"].lower()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("case", "service_attr"),
    [
        ("patch", "update_deployed_agent"),
        ("deploy", "deploy_deployed_agent"),
        ("pause", "pause_deployed_agent"),
    ],
)
async def test_management_routes_map_invalid_transition_value_errors_to_conflict(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    service_attr: str,
):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = _owner_user

    async def fake_invalid_transition(**kwargs):
        raise ValueError("Unsupported deployed-agent state transition: live -> draft.")

    monkeypatch.setattr(routes_deployed_agents.deployed_agent_service, service_attr, fake_invalid_transition)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await _invoke_management_route(client, case)

    assert response.status_code == 409
    assert "transition" in response.json()["detail"].lower()


@pytest.mark.anyio
async def test_list_deployed_agents_route_returns_items(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = lambda: {"user_id": "user-1"}

    async def fake_list_deployed_agents(**kwargs):
        assert kwargs["owner_workspace_id"] == "ws-1"
        assert kwargs["deployment_state"] == "draft"
        return {"items": [_created_payload()]}

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "list_deployed_agents",
        fake_list_deployed_agents,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/deployed-agents", params={"workspace_id": "ws-1", "deployment_state": "draft"})

    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == "dagent_1"


@pytest.mark.anyio
async def test_get_deployed_agent_route_returns_not_found_when_missing(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = lambda: {"user_id": "user-1"}

    async def fake_get_deployed_agent_detail(**kwargs):
        assert kwargs["deployed_agent_id"] == "dagent_missing"
        return None

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "get_deployed_agent_detail",
        fake_get_deployed_agent_detail,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/deployed-agents/dagent_missing", params={"workspace_id": "ws-1"})

    assert response.status_code == 404


@pytest.mark.anyio
async def test_list_deployed_agent_analytics_route_returns_items(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = lambda: {"user_id": "user-1"}

    async def fake_list_deployed_agent_analytics(**kwargs):
        assert kwargs["owner_workspace_id"] == "ws-1"
        return {"items": [_analytics_payload()]}

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "list_deployed_agent_analytics",
        fake_list_deployed_agent_analytics,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/deployed-agents/analytics", params={"workspace_id": "ws-1"})

    assert response.status_code == 200
    assert response.json()["items"][0]["deployed_agent_id"] == "dagent_1"


@pytest.mark.anyio
async def test_get_deployed_agent_analytics_route_returns_payload(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = lambda: {"user_id": "user-1"}

    async def fake_get_deployed_agent_analytics(**kwargs):
        assert kwargs["deployed_agent_id"] == "dagent_1"
        assert kwargs["owner_workspace_id"] == "ws-1"
        return _analytics_payload()

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "get_deployed_agent_analytics",
        fake_get_deployed_agent_analytics,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/deployed-agents/dagent_1/analytics", params={"workspace_id": "ws-1"})

    assert response.status_code == 200
    assert response.json()["active_users_last_30d"] == 12


@pytest.mark.anyio
async def test_get_deployed_agent_admin_dashboard_route_returns_payload(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = _owner_user

    async def fake_get_dashboard(**kwargs):
        assert kwargs["agent_id"] == "dagent_1"
        assert kwargs["workspace_id"] == "ws-1"
        current_user = kwargs["current_user"]
        assert isinstance(current_user, dict)
        assert current_user["user_id"] == "user-owner"
        return _admin_dashboard_payload()

    monkeypatch.setattr(
        routes_deployed_agents,
        "get_deployed_agent_admin_dashboard_service",
        lambda: type("DashboardService", (), {"get_dashboard": staticmethod(fake_get_dashboard)})(),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/deployed-agents/dagent_1/admin-dashboard",
            params={"workspace_id": "ws-1"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["orders_today"] == 2
    assert payload["customer_entry"]["bot_username"] == "bluebirdcafe_bot"
    assert payload["specialist_profile"]["channel"]["primary"] == "telegram_bot"


@pytest.mark.anyio
async def test_get_deployed_agent_analytics_route_returns_not_found_when_missing(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = lambda: {"user_id": "user-1"}

    async def fake_get_deployed_agent_analytics(**kwargs):
        assert kwargs["deployed_agent_id"] == "dagent_missing"
        return None

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "get_deployed_agent_analytics",
        fake_get_deployed_agent_analytics,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/deployed-agents/dagent_missing/analytics", params={"workspace_id": "ws-1"})

    assert response.status_code == 404


@pytest.mark.anyio
async def test_patch_deployed_agent_route_returns_updated_payload(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = lambda: {"user_id": "user-1"}

    async def fake_update_deployed_agent(**kwargs):
        assert kwargs["deployed_agent_id"] == "dagent_1"
        assert kwargs["updates"]["name"] == "Store Concierge"
        payload = _created_payload()
        payload["name"] = "Store Concierge"
        return payload

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "update_deployed_agent",
        fake_update_deployed_agent,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.patch(
            "/deployed-agents/dagent_1",
            json={"workspace_id": "ws-1", "name": "Store Concierge"},
        )

    assert response.status_code == 200
    assert response.json()["name"] == "Store Concierge"


@pytest.mark.anyio
async def test_deploy_deployed_agent_route_returns_live_payload(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = lambda: {"user_id": "user-1"}

    async def fake_deploy_deployed_agent(**kwargs):
        assert kwargs["deployed_agent_id"] == "dagent_1"
        payload = _created_payload()
        payload["deployment_state"] = "live"
        return payload

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "deploy_deployed_agent",
        fake_deploy_deployed_agent,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/deployed-agents/dagent_1/deploy",
            json={"workspace_id": "ws-1"},
        )

    assert response.status_code == 200
    assert response.json()["deployment_state"] == "live"


@pytest.mark.anyio
async def test_pause_deployed_agent_route_returns_paused_payload(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = lambda: {"user_id": "user-1"}

    async def fake_pause_deployed_agent(**kwargs):
        assert kwargs["deployed_agent_id"] == "dagent_1"
        payload = _created_payload()
        payload["deployment_state"] = "paused"
        return payload

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "pause_deployed_agent",
        fake_pause_deployed_agent,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/deployed-agents/dagent_1/pause",
            json={"workspace_id": "ws-1"},
        )

    assert response.status_code == 200
    assert response.json()["deployment_state"] == "paused"


@pytest.mark.anyio
async def test_kill_deployed_agent_route_returns_suspended_payload(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = lambda: {"user_id": "user-1"}

    async def fake_kill_deployed_agent(**kwargs):
        assert kwargs["deployed_agent_id"] == "dagent_1"
        assert kwargs["owner_workspace_id"] == "ws-1"
        assert kwargs["reason"] == "incident"
        payload = _created_payload()
        payload["deployment_state"] = "suspended"
        payload["metadata"] = {"kill_switch": {"active": True}}
        return payload

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "kill_deployed_agent",
        fake_kill_deployed_agent,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/deployed-agents/dagent_1/kill",
            json={"workspace_id": "ws-1", "reason": "incident"},
        )

    assert response.status_code == 200
    assert response.json()["deployment_state"] == "suspended"
    assert response.json()["metadata"]["kill_switch"]["active"] is True


@pytest.mark.anyio
async def test_recovery_action_route_revokes_connector_access(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = lambda: {"user_id": "user-1"}

    async def fake_recovery_action(**kwargs):
        assert kwargs["deployed_agent_id"] == "dagent_1"
        assert kwargs["owner_workspace_id"] == "ws-1"
        assert kwargs["action"] == "revoke_connector_access"
        payload = _created_payload()
        payload["channels"] = {"telegram": {"enabled": False}}
        return payload

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "apply_deployed_agent_recovery_action",
        fake_recovery_action,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/deployed-agents/dagent_1/recovery-actions",
            json={"workspace_id": "ws-1", "action": "revoke_connector_access"},
        )

    assert response.status_code == 200
    assert response.json()["channels"]["telegram"]["enabled"] is False


@pytest.mark.anyio
async def test_runtime_session_kill_route_terminates_session(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = lambda: {"user_id": "user-1"}

    async def fake_kill_runtime_session(**kwargs):
        assert kwargs["deployed_agent_id"] == "dagent_1"
        assert kwargs["session_id"] == "sess-1"
        assert kwargs["owner_workspace_id"] == "ws-1"
        return {"ok": True, "deployed_agent_id": "dagent_1", "session_id": "sess-1", "status": "killed"}

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "kill_deployed_agent_runtime_session",
        fake_kill_runtime_session,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/deployed-agents/dagent_1/runtime-sessions/sess-1/kill",
            json={"workspace_id": "ws-1"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "killed"


@pytest.mark.anyio
async def test_workspace_emergency_stop_route_returns_stopped_agents(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = lambda: {"user_id": "user-1"}

    async def fake_emergency_stop(**kwargs):
        assert kwargs["owner_workspace_id"] == "ws-1"
        assert kwargs["reason"] == "incident"
        return {
            "ok": True,
            "workspace_id": "ws-1",
            "status": "emergency_stopped",
            "stopped_agent_ids": ["dagent_1"],
            "stopped_run_ids": ["run-1"],
            "count": 1,
        }

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "emergency_stop_workspace_deployed_agents",
        fake_emergency_stop,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/deployed-agents/emergency-stop",
            json={"workspace_id": "ws-1", "reason": "incident"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "emergency_stopped"
    assert response.json()["stopped_run_ids"] == ["run-1"]


@pytest.mark.anyio
async def test_audit_export_route_returns_activity_log(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = lambda: {"user_id": "user-1"}
    monkeypatch.setattr(
        routes_deployed_agents,
        "_enforce_deployed_agent_route_decision",
        lambda **_kwargs: {"next_action": "export_deployed_agent_audit_logs"},
    )

    async def fake_audit_export(**kwargs):
        assert kwargs["deployed_agent_id"] == "dagent_1"
        assert kwargs["owner_workspace_id"] == "ws-1"
        assert kwargs["limit"] == 25
        return {
            "schema_version": 1,
            "deployed_agent_id": "dagent_1",
            "workspace_id": "ws-1",
            "audit_log": {"items": [{"action": "deployed_agent_deployed_live"}]},
        }

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "export_deployed_agent_audit_logs",
        fake_audit_export,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/deployed-agents/dagent_1/audit-export",
            params={"workspace_id": "ws-1", "limit": 25},
        )

    assert response.status_code == 200
    assert response.json()["audit_log"]["items"][0]["action"] == "deployed_agent_deployed_live"


@pytest.mark.anyio
async def test_list_deployed_agents_route_forbids_member_access(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = lambda: {
        "user_id": "user-member",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-1": {
                "workspace_id": "ws-1",
                "tenant_id": "tenant-1",
                "role": "member",
            }
        },
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/deployed-agents", params={"workspace_id": "ws-1"})

    assert response.status_code == 403


@pytest.mark.anyio
async def test_deploy_deployed_agent_route_propagates_service_conflict(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = lambda: {"user_id": "user-1"}

    async def fake_deploy_deployed_agent(**kwargs):
        raise HTTPException(status_code=409, detail="Only Telegram may be activated for live deployment in Phase 2.")

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "deploy_deployed_agent",
        fake_deploy_deployed_agent,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/deployed-agents/dagent_1/deploy",
            json={"workspace_id": "ws-1"},
        )

    assert response.status_code == 409


@pytest.mark.anyio
async def test_list_deployed_agent_conversations_route_returns_paginated_items(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = lambda: {"user_id": "user-1"}

    async def fake_list_deployed_agent_conversations(**kwargs):
        assert kwargs["deployed_agent_id"] == "dagent_1"
        assert kwargs["owner_workspace_id"] == "ws-1"
        assert kwargs["limit"] == 25
        assert kwargs["offset"] == 10
        return _conversation_list_payload()

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "list_deployed_agent_conversations",
        fake_list_deployed_agent_conversations,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/deployed-agents/dagent_1/conversations",
            params={"workspace_id": "ws-1", "limit": 25, "offset": 10},
        )

    assert response.status_code == 200
    assert response.json()["items"][0]["session_id"] == "sess-1"


@pytest.mark.anyio
async def test_list_deployed_agent_activity_route_returns_paginated_items(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = lambda: {"user_id": "user-1"}

    async def fake_list_deployed_agent_activity(**kwargs):
        assert kwargs["deployed_agent_id"] == "dagent_1"
        assert kwargs["owner_workspace_id"] == "ws-1"
        assert kwargs["limit"] == 20
        assert kwargs["offset"] == 5
        return {
            "deployed_agent_id": "dagent_1",
            "items": [
                {
                    "id": "aevt-1",
                    "kind": "lifecycle",
                    "action": "deployed_agent_updated",
                    "summary": "Updated settings",
                }
            ],
            "offset": 5,
            "limit": 20,
            "count": 1,
            "total": 1,
            "has_more": False,
            "summary": {"review_required_count": 0, "by_kind": {"lifecycle": 1}},
        }

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "list_deployed_agent_activity",
        fake_list_deployed_agent_activity,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/deployed-agents/dagent_1/activity",
            params={"workspace_id": "ws-1", "limit": 20, "offset": 5},
        )

    assert response.status_code == 200
    assert response.json()["items"][0]["kind"] == "lifecycle"


@pytest.mark.anyio
async def test_list_deployed_agent_activity_route_returns_404_when_missing(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = lambda: {"user_id": "user-1"}

    async def fake_list_deployed_agent_activity(**kwargs):
        return None

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "list_deployed_agent_activity",
        fake_list_deployed_agent_activity,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/deployed-agents/dagent_1/activity",
            params={"workspace_id": "ws-1"},
        )

    assert response.status_code == 404


@pytest.mark.anyio
async def test_get_deployed_agent_conversation_detail_route_returns_transcript(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = lambda: {"user_id": "user-1"}

    async def fake_get_deployed_agent_conversation_detail(**kwargs):
        assert kwargs["deployed_agent_id"] == "dagent_1"
        assert kwargs["session_id"] == "sess-1"
        return _conversation_detail_payload()

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "get_deployed_agent_conversation_detail",
        fake_get_deployed_agent_conversation_detail,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/deployed-agents/dagent_1/conversations/sess-1",
            params={"workspace_id": "ws-1"},
        )

    assert response.status_code == 200
    assert response.json()["tool_calls"][0]["tool_name"] == "telegram_bot.send_message"


@pytest.mark.anyio
async def test_get_deployed_agent_conversations_route_forbids_cross_workspace_member_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = _member_user

    async def fake_forbidden_list_deployed_agent_conversations(**kwargs):
        current_user = kwargs.get("current_user")
        assert isinstance(current_user, dict)
        assert current_user["user_id"] == "user-member"
        assert kwargs["deployed_agent_id"] == "dagent_1"
        assert kwargs["owner_workspace_id"] == "ws-1"
        raise HTTPException(status_code=403, detail="Owner role required for workspace 'ws-1'.")

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "list_deployed_agent_conversations",
        fake_forbidden_list_deployed_agent_conversations,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/deployed-agents/dagent_1/conversations",
            params={"workspace_id": "ws-1"},
        )

    assert response.status_code == 403


@pytest.mark.anyio
async def test_list_deployed_agent_business_insights_route_returns_owner_payload(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = _owner_user

    async def fake_list_owner_business_insights(**kwargs):
        assert kwargs["workspace_id"] == "ws-1"
        assert kwargs["deployed_agent_id"] == "dagent_1"
        assert kwargs["status"] == "candidate"
        current_user = kwargs["current_user"]
        assert isinstance(current_user, dict)
        assert current_user["auth_type"] == "bearer"
        return _business_insights_payload()

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_business_insights_service,
        "list_owner_business_insights",
        fake_list_owner_business_insights,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/deployed-agents/dagent_1/business-insights",
            params={"workspace_id": "ws-1", "status": "candidate"},
        )

    assert response.status_code == 200
    assert response.json()["items"][0]["sensitivity"] == "orange"


@pytest.mark.anyio
async def test_approve_deployed_agent_business_insight_route_validates_csrf_and_owner_note(
    monkeypatch: pytest.MonkeyPatch,
):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = _owner_user
    monkeypatch.setattr(routes_deployed_agents.auth_module, "validate_csrf", lambda request: None)

    async def fake_review_owner_business_insight(**kwargs):
        assert kwargs["workspace_id"] == "ws-1"
        assert kwargs["deployed_agent_id"] == "dagent_1"
        assert kwargs["insight_id"] == "bins_1"
        assert kwargs["status"] == "approved"
        assert kwargs["note"] == "Use this in the owner policy draft."
        return {"insight": {"id": "bins_1", "status": "approved"}}

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_business_insights_service,
        "review_owner_business_insight",
        fake_review_owner_business_insight,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/deployed-agents/dagent_1/business-insights/bins_1/approve",
            params={"workspace_id": "ws-1"},
            json={"note": "Use this in the owner policy draft."},
        )

    assert response.status_code == 200
    assert response.json()["insight"]["status"] == "approved"


@pytest.mark.anyio
async def test_shop_evaluate_route_returns_catalog_answer_and_persists_activity(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = _owner_user
    monkeypatch.setattr(routes_deployed_agents.auth_module, "validate_csrf", lambda request: None)

    async def fake_evaluate(**kwargs):
        return {
            "ok": True,
            "status": "answered",
            "answer": "We have the Blue Widget in stock for $29.99.",
            "approval": {"required": False},
            "activity_events": [
                {
                    "event": "studio.proof.shop_assistant.inventory_hit",
                    "actor_type": "deployed_agent",
                    "actor_id": "dagent_1",
                    "status": "logged",
                    "customer_message_summary": "Do you have blue widget",
                    "metadata": {"matches": 1},
                }
            ],
            "roi_metrics": {"questions_handled": 1, "inventory_hits": 1},
            "activity_billing_evidence": {
                "visible_activity": {"summary": "We have the Blue Widget in stock for $29.99."},
                "credit_line_items": [{"credit_item_type": "ai_pro_tokens", "quantity": 900}],
            },
        }

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "evaluate_deployed_shop_assistant_customer_question",
        fake_evaluate,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/deployed-agents/dagent_1/shop-evaluate",
            json={"workspace_id": "ws-1", "customer_message": "Do you have blue widget?"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert "Blue Widget" in payload["answer"]
    assert payload["approval"]["required"] is False


@pytest.mark.anyio
async def test_shop_evaluate_route_returns_approval_gate_for_discount(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = _owner_user
    monkeypatch.setattr(routes_deployed_agents.auth_module, "validate_csrf", lambda request: None)

    async def fake_evaluate(**kwargs):
        return {
            "ok": True,
            "status": "approval_required",
            "answer": "Owner approval is required before offering a discount.",
            "approval": {"required": True, "gate": "discount", "risk_class": "ORANGE"},
            "activity_events": [
                {
                    "event": "studio.proof.shop_assistant.owner_approval_required",
                    "actor_type": "deployed_agent",
                    "actor_id": "dagent_1",
                    "status": "approval_required",
                    "approval_required": True,
                    "metadata": {"approval_gate": "discount"},
                }
            ],
            "roi_metrics": {"questions_handled": 1, "escalations": 1},
        }

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "evaluate_deployed_shop_assistant_customer_question",
        fake_evaluate,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/deployed-agents/dagent_1/shop-evaluate",
            json={"workspace_id": "ws-1", "customer_message": "Can I get a discount?"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["approval"]["required"] is True
    assert payload["approval"]["gate"] == "discount"


@pytest.mark.anyio
async def test_shop_evaluate_service_creates_durable_approval_for_all_gates(monkeypatch: pytest.MonkeyPatch):
    """Verify that each approval gate creates a durable approval request in run_approvals."""
    gates = ["discount", "refund", "payment_link", "mass_message"]
    captured_approvals: list[dict] = []

    async def fake_get_workspace(workspace_id):
        return {"tenant_id": "tenant-1", "workspace_id": "ws-1"}

    async def fake_get_deployed_agent(deployed_agent_id, tenant_id, owner_workspace_id):
        return {
            "id": "dagent_1",
            "tenant_id": "tenant-1",
            "owner_workspace_id": "ws-1",
            "metadata": {"public_tier": "pro", "effective_provider": "openai", "effective_model": "gpt-4o"},
        }

    async def fake_append_activity_event(**kwargs):
        return None

    async def fake_upsert_insight(**kwargs):
        return None

    monkeypatch.setattr(control_plane_repository, "get_workspace_by_id", fake_get_workspace)
    monkeypatch.setattr(control_plane_repository, "get_deployed_agent_by_id", fake_get_deployed_agent)
    monkeypatch.setattr(activity_ledger_service, "append_activity_event", fake_append_activity_event)
    monkeypatch.setattr(control_plane_repository, "upsert_deployed_agent_business_insight_candidate", fake_upsert_insight)

    def fake_create_approval(run_id, approval_id, request_payload, actor, trace_id, *, metadata=None, expires_at=None):
        captured_approvals.append({
            "run_id": run_id,
            "approval_id": approval_id,
            "request_payload": request_payload,
            "metadata": metadata,
        })
        return {"approval_id": approval_id}

    monkeypatch.setattr(
        run_state_repository,
        "sync_create_or_update_approval_request",
        fake_create_approval,
    )

    for gate in gates:
        captured_approvals.clear()

        def fake_revenue_eval(deployed_agent, workspace_id, customer_message, connector_id=None):
            return {
                "ok": True,
                "status": "approval_required",
                "answer": f"Owner approval required for {gate}.",
                "approval": {"required": True, "gate": gate, "risk_class": "ORANGE"},
                "activity_events": [],
                "roi_metrics": {"questions_handled": 1, "escalations": 1},
            }

        monkeypatch.setattr(
            shop_assistant_revenue_agent_service,
            "evaluate_shop_assistant_customer_question",
            fake_revenue_eval,
        )

        result = await deployed_agent_service.evaluate_deployed_shop_assistant_customer_question(
            deployed_agent_id="dagent_1",
            current_user={
                "user_id": "user-owner",
                "workspace_access": {"ws-1": {"role": "owner", "tenant_id": "tenant-1"}},
            },
            owner_workspace_id="ws-1",
            customer_message=f"Can I get a {gate}?",
            connector_id="telegram_1",
        )

        assert result["approval"]["required"] is True
        assert result["approval"]["gate"] == gate
        assert "approval_id" in result
        assert len(captured_approvals) == 1
        approval = captured_approvals[0]
        assert approval["run_id"] == "dagent_1"
        assert approval["request_payload"]["actions"] == [gate]
        assert approval["request_payload"]["target"] == "dagent_1"
        assert approval["metadata"]["source_type"] == "deployed_agent"
        assert approval["metadata"]["gate"] == gate
        assert approval["metadata"]["connector_id"] == "telegram_1"
        assert approval["metadata"]["channel_key"] == "telegram_1"


@pytest.mark.anyio
async def test_shop_evaluate_service_fails_closed_when_approval_persistence_fails(monkeypatch: pytest.MonkeyPatch):
    async def fake_get_workspace(workspace_id):
        return {"tenant_id": "tenant-1", "workspace_id": "ws-1"}

    async def fake_get_deployed_agent(deployed_agent_id, tenant_id, owner_workspace_id):
        return {
            "id": "dagent_1",
            "tenant_id": "tenant-1",
            "owner_workspace_id": "ws-1",
            "metadata": {"public_tier": "pro", "effective_provider": "openai", "effective_model": "gpt-4o"},
        }

    async def fake_append_activity_event(**kwargs):
        return None

    async def fake_upsert_insight(**kwargs):
        return None

    def fake_create_approval(*args, **kwargs):
        raise RuntimeError("approval store unavailable")

    def fake_revenue_eval(deployed_agent, workspace_id, customer_message, connector_id=None):
        return {
            "ok": True,
            "status": "approval_required",
            "answer": "Owner approval required before offering a discount.",
            "approval": {"required": True, "gate": "discount", "risk_class": "ORANGE"},
            "activity_events": [],
            "roi_metrics": {"questions_handled": 1, "escalations": 1},
        }

    monkeypatch.setattr(control_plane_repository, "get_workspace_by_id", fake_get_workspace)
    monkeypatch.setattr(control_plane_repository, "get_deployed_agent_by_id", fake_get_deployed_agent)
    monkeypatch.setattr(activity_ledger_service, "append_activity_event", fake_append_activity_event)
    monkeypatch.setattr(control_plane_repository, "upsert_deployed_agent_business_insight_candidate", fake_upsert_insight)
    monkeypatch.setattr(run_state_repository, "sync_create_or_update_approval_request", fake_create_approval)
    monkeypatch.setattr(
        shop_assistant_revenue_agent_service,
        "evaluate_shop_assistant_customer_question",
        fake_revenue_eval,
    )

    result = await deployed_agent_service.evaluate_deployed_shop_assistant_customer_question(
        deployed_agent_id="dagent_1",
        current_user={
            "user_id": "user-owner",
            "workspace_access": {"ws-1": {"role": "owner", "tenant_id": "tenant-1"}},
        },
        owner_workspace_id="ws-1",
        customer_message="Can I get a discount?",
        connector_id="telegram_1",
    )

    assert result["status"] == "approval_unavailable"
    assert result["approval"]["workflow_error"] == "approval_creation_failed"
    assert "approval_id" not in result
    assert "temporarily unavailable" in result["answer"]


@pytest.mark.anyio
async def test_shop_evaluate_service_persists_real_billing_ledger(monkeypatch: pytest.MonkeyPatch):
    """Verify that shop evaluation writes real usage line items to deployed_agent_monthly_cost_ledger."""
    from server_modules import usage_accounting_service

    async def fake_get_workspace(workspace_id):
        return {"tenant_id": "tenant-1", "workspace_id": "ws-1"}

    async def fake_get_deployed_agent(deployed_agent_id, tenant_id, owner_workspace_id):
        return {
            "id": "dagent_1",
            "tenant_id": "tenant-1",
            "owner_workspace_id": "ws-1",
            "metadata": {"public_tier": "pro", "effective_provider": "openai", "effective_model": "gpt-4o"},
        }

    async def fake_append_activity_event(**kwargs):
        return None

    async def fake_upsert_insight(**kwargs):
        return None

    monkeypatch.setattr(control_plane_repository, "get_workspace_by_id", fake_get_workspace)
    monkeypatch.setattr(control_plane_repository, "get_deployed_agent_by_id", fake_get_deployed_agent)
    monkeypatch.setattr(activity_ledger_service, "append_activity_event", fake_append_activity_event)
    monkeypatch.setattr(control_plane_repository, "upsert_deployed_agent_business_insight_candidate", fake_upsert_insight)

    captured_ledger_entries: list[dict] = []

    async def fake_record_ledger(**kwargs):
        captured_ledger_entries.append(dict(kwargs))

    monkeypatch.setattr(control_plane_repository, "record_deployed_agent_monthly_cost_ledger_entry", fake_record_ledger)

    def fake_build_usage_record(*, run_id, tenant_id, workspace_id, deployed_agent_id, source_surface, effective_provider, effective_model, input_tokens, output_tokens, total_tokens, allow_provider_fallback):
        return {
            "provider": effective_provider,
            "model": effective_model,
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": total_tokens,
            "estimated_cost_usd": 0.0045,
            "pricing_known": True,
            "pricing_source": "pricing_registry",
        }

    monkeypatch.setattr(usage_accounting_service, "build_usage_accounting_record", fake_build_usage_record)

    def fake_revenue_eval(deployed_agent, workspace_id, customer_message, connector_id=None):
        return {
            "ok": True,
            "status": "answered",
            "answer": "Blue Widget is $29.99.",
            "approval": {"required": False},
            "catalog": {"source": {"connector_type": "google_sheets", "connector_id": "gsheet-1"}},
            "activity_events": [],
            "roi_metrics": {"questions_handled": 1, "inventory_hits": 1},
        }

    monkeypatch.setattr(
        shop_assistant_revenue_agent_service,
        "evaluate_shop_assistant_customer_question",
        fake_revenue_eval,
    )

    result = await deployed_agent_service.evaluate_deployed_shop_assistant_customer_question(
        deployed_agent_id="dagent_1",
        current_user={
            "user_id": "user-owner",
            "workspace_access": {"ws-1": {"role": "owner", "tenant_id": "tenant-1"}},
        },
        owner_workspace_id="ws-1",
        customer_message="Do you have blue widget?",
        connector_id="telegram_1",
    )

    assert result["ok"] is True
    assert len(captured_ledger_entries) == 2

    ai_entry = next(e for e in captured_ledger_entries if e.get("metadata", {}).get("credit_item_type", "").startswith("ai_"))
    connector_entry = next(e for e in captured_ledger_entries if e.get("metadata", {}).get("credit_item_type") == "connector_read")

    assert ai_entry["tenant_id"] == "tenant-1"
    assert ai_entry["workspace_id"] == "ws-1"
    assert ai_entry["deployed_agent_id"] == "dagent_1"
    assert ai_entry["run_status"] == "completed"
    assert ai_entry["provider"] == "openai"
    assert ai_entry["model"] == "gpt-4o"
    assert ai_entry["total_tokens"] > 0
    assert ai_entry["estimated_cost_usd"] == 0.0045
    assert ai_entry["metadata"]["source_surface"] == "shop_assistant"
    assert ai_entry["metadata"]["credit_item_type"].startswith("ai_")

    assert connector_entry["tenant_id"] == "tenant-1"
    assert connector_entry["workspace_id"] == "ws-1"
    assert connector_entry["deployed_agent_id"] == "dagent_1"
    assert connector_entry["total_tokens"] == 0
    assert connector_entry["estimated_cost_usd"] == 0.0
    assert connector_entry["metadata"]["credit_item_type"] == "connector_read"
    assert connector_entry["metadata"]["connector_kind"] == "google_sheets"
    assert connector_entry["metadata"]["connector_id"] == "gsheet-1"


@pytest.mark.anyio
async def test_deployed_agent_evaluate_route_rejects_missing_csrf(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /evaluate must return 403 when CSRF validation fails."""
    from fastapi import HTTPException
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = _owner_user

    def _reject_csrf(request):
        raise HTTPException(status_code=403, detail="CSRF validation failed.")

    monkeypatch.setattr(routes_deployed_agents.auth_module, "validate_csrf", _reject_csrf)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/deployed-agents/dagent_1/shop-evaluate",
            json={"workspace_id": "ws-1", "customer_message": "hello", "connector_id": "telegram_1"},
        )

    assert response.status_code == 403
    assert "csrf" in response.text.lower() or "CSRF" in response.text


@pytest.mark.anyio
async def test_upload_deployed_agent_knowledge_file_route(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = _owner_user

    captured: dict[str, object] = {}

    async def fake_upload_deployed_agent_knowledge_file(**kwargs):
        captured.update(kwargs)
        return {
            "workspace_id": kwargs["owner_workspace_id"],
            "deployed_agent_id": kwargs["deployed_agent_id"],
            "knowledge_source": {
                "id": "knowledge-1",
                "uri": "knowledge://business-agents/dagent_1/faq.md",
                "label": "faq.md",
                "kind": "file",
            },
            "deployed_agent": {
                **_created_payload(),
                "knowledge_sources": [
                    {"id": "knowledge-1", "uri": "knowledge://business-agents/dagent_1/faq.md"}
                ],
            },
        }

    monkeypatch.setattr(
        routes_deployed_agents.deployed_agent_service,
        "upload_deployed_agent_knowledge_file",
        fake_upload_deployed_agent_knowledge_file,
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/deployed-agents/dagent_1/knowledge/files",
            json={"workspace_id": "ws-1", "file_name": "faq.md", "content_text": "# FAQ"},
        )

    assert response.status_code == 200
    assert captured["deployed_agent_id"] == "dagent_1"
    assert captured["owner_workspace_id"] == "ws-1"
    assert captured["file_name"] == "faq.md"
    assert captured["content_text"] == "# FAQ"
    assert response.json()["knowledge_source"]["uri"] == "knowledge://business-agents/dagent_1/faq.md"

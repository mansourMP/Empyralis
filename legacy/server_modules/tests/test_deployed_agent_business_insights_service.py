from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from server_modules import control_plane_repository
from server_modules import deployed_agent_business_insights_service as service


def test_detect_pricing_insight_redacts_pii_and_never_allows_price_action() -> None:
    candidates = service.detect_business_insight_candidates(
        user_message="I am outside now, need it right now. Can you price match? call +1 415 555 1234 or me@example.com",
        channel_key="whatsapp",
    )

    keys = {item["pattern_key"] for item in candidates}
    assert "pricing.price_match_or_discount_pressure" in keys
    assert "pricing.urgent_price_insensitive_signal" in keys
    for item in candidates:
        rendered = " ".join(item.get("redacted_examples") or [])
        assert "415 555 1234" not in rendered
        assert "me@example.com" not in rendered
        if item["insight_type"] == "pricing_intelligence":
            assert item["sensitivity"] == "orange"
            assert item["metadata"]["pricing_action_allowed"] is False
    assert "my name is John Doe".lower() not in " ".join(
        " ".join(item.get("redacted_examples") or []).lower() for item in candidates
    )


@pytest.mark.anyio
async def test_record_turn_insights_stores_aggregate_candidate_without_external_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service.entitlements_service,
        "workspace_entitlement_payload_for_workspace_id",
        lambda workspace_id: {"capabilities": {"advanced_features_enabled": True}},
    )
    recorded_calls: list[dict[str, object]] = []

    async def fake_upsert(**kwargs):
        recorded_calls.append(dict(kwargs))
        return {"id": "bins-1", **kwargs}

    monkeypatch.setattr(
        service.control_plane_repository,
        "upsert_deployed_agent_business_insight_candidate",
        fake_upsert,
    )

    payload = await service.record_deployed_agent_turn_insights(
        tenant_id="tenant-1",
        workspace_id="ws-1",
        deployed_agent_id="dagent-1",
        channel_key="telegram",
        user_message="Do you have size 42 in stock? I want to buy today. Authorization: Bearer abc.def.ghi",
        assistant_reply="I can check live inventory.",
    )

    assert payload
    assert {item["pattern_key"] for item in recorded_calls} == {
        "operations.inventory_availability_questions",
        "revenue.purchase_or_booking_intent",
    }
    for call in recorded_calls:
        assert "external_user_id" not in call
        assert "session_id" not in call
        assert call["tenant_id"] == "tenant-1"
        assert call["workspace_id"] == "ws-1"
        assert call["deployed_agent_id"] == "dagent-1"
        rendered = " ".join(call.get("redacted_examples") or [])
        assert "abc.def.ghi" not in rendered
        assert "[redacted]" in rendered or "[redacted-secret]" in rendered
        assert "john doe" not in rendered.lower()
        assert "external_user_id" not in call.get("metadata", {})


@pytest.mark.anyio
async def test_record_turn_insights_filters_forbidden_metadata_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service.entitlements_service,
        "workspace_entitlement_payload_for_workspace_id",
        lambda workspace_id: {"capabilities": {"advanced_features_enabled": True}},
    )

    async def fake_upsert(**kwargs):
        return kwargs

    monkeypatch.setattr(
        service.control_plane_repository,
        "upsert_deployed_agent_business_insight_candidate",
        fake_upsert,
    )

    monkeypatch.setattr(
        service,
        "detect_business_insight_candidates",
        lambda **_kwargs: [
            {
                "pattern_key": "operations.inventory_availability_questions",
                "insight_type": "business_pattern",
                "title": "Inventory questions",
                "summary": "Customers ask for stock details",
                "recommendation": "Connect live inventory",
                "sensitivity": "yellow",
                "confidence": 0.6,
                "redacted_examples": ["my name is John Doe"],
                "metadata": {
                    "external_user_id": "customer-1",
                    "session_id": "sess-1",
                    "phone": "+1 415 555 1234",
                    "channel_key": "telegram",
                },
            }
        ],
    )

    payload = await service.record_deployed_agent_turn_insights(
        tenant_id="tenant-1",
        workspace_id="ws-1",
        deployed_agent_id="dagent-1",
        channel_key="telegram",
        user_message="hello",
        assistant_reply="ok",
    )

    assert payload
    stored = payload[0]
    metadata = stored.get("metadata") or {}
    assert "external_user_id" not in metadata
    assert "session_id" not in metadata
    assert "phone" not in metadata
    assert metadata.get("channel_key") == "telegram"


@pytest.mark.anyio
async def test_record_turn_insights_is_premium_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service.entitlements_service,
        "workspace_entitlement_payload_for_workspace_id",
        lambda workspace_id: {"capabilities": {"advanced_features_enabled": False}},
    )
    upsert = AsyncMock()
    monkeypatch.setattr(
        service.control_plane_repository,
        "upsert_deployed_agent_business_insight_candidate",
        upsert,
    )

    payload = await service.record_deployed_agent_turn_insights(
        tenant_id="tenant-1",
        workspace_id="ws-1",
        deployed_agent_id="dagent-1",
        channel_key="telegram",
        user_message="Can you price match this today?",
    )

    assert payload == []
    upsert.assert_not_called()


@pytest.mark.anyio
async def test_owner_review_rejects_service_key_identity() -> None:
    with pytest.raises(HTTPException) as exc:
        await service.list_owner_business_insights(
            current_user={"user_id": "svc", "auth_type": "api_key", "role": "owner"},
            workspace_id="ws-1",
            deployed_agent_id="dagent-1",
        )

    assert exc.value.status_code == 403
    assert "Real authenticated owner session required" in str(exc.value.detail)


def test_business_insight_schema_excludes_external_user_identity() -> None:
    schema = control_plane_repository.CONTROL_PLANE_SCHEMA_SQL
    table_sql = schema.split("CREATE TABLE IF NOT EXISTS deployed_agent_business_insights", 1)[1].split(
        "CREATE TABLE IF NOT EXISTS external_user_privacy_requests",
        1,
    )[0]

    assert "external_user_id" not in table_sql
    assert "session_key" not in table_sql
    assert "redacted_examples JSONB" in table_sql

from __future__ import annotations

from server_modules import credit_ledger_contract


def test_hosted_light_tier_maps_to_light_token_line_item() -> None:
    item = credit_ledger_contract.build_credit_ledger_line_item(
        public_tier="light",
        billing_source="empyralis_credits",
        total_tokens=2400,
    )

    assert item["credit_item_type"] == "ai_light_tokens"
    assert item["credit_type"] == "ai_tokens"
    assert item["quantity"] == 2400.0
    assert item["quantity_unit"] == "tokens"
    assert item["billing_source"] == "empyralis_credits"
    assert item["credit_multiplier"] == 0.5


def test_hosted_max_tier_maps_to_max_token_line_item() -> None:
    item = credit_ledger_contract.build_credit_ledger_line_item(
        public_tier="max",
        billing_source="empyralis_credits",
        total_tokens=900,
    )

    assert item["credit_item_type"] == "ai_max_tokens"
    assert item["credit_multiplier"] == 2.0
    assert item["quantity"] == 900.0


def test_local_ai_maps_without_hosted_credit_spend() -> None:
    item = credit_ledger_contract.build_credit_ledger_line_item(
        public_tier="local_ai",
        billing_source="local_runtime",
        total_tokens=1200,
    )

    assert item["credit_item_type"] == "local_ai_tokens"
    assert item["credit_type"] == "ai_tokens"
    assert item["billing_source"] == "local_runtime"
    assert item["credit_multiplier"] == 0.0


def test_my_api_key_maps_to_custom_usage_item() -> None:
    item = credit_ledger_contract.build_credit_ledger_line_item(
        public_tier="my_api_key",
        billing_source="user_api_key",
        total_tokens=500,
    )

    assert item["credit_item_type"] == "custom_api_key_usage"
    assert item["credit_type"] == "custom_api_key_usage"
    assert item["billing_source"] == "user_api_key"
    assert item["quantity_unit"] == "events"


def test_virtual_runtime_minutes_use_runtime_line_items() -> None:
    item = credit_ledger_contract.build_credit_ledger_line_item(
        metadata={"runtime_type": "virtual_browser"},
        runtime_minutes=17.5,
        billing_source="virtual_runtime_credits",
    )

    assert item["credit_item_type"] == "virtual_browser_minutes"
    assert item["credit_type"] == "computer_runtime"
    assert item["quantity"] == 17.5
    assert item["quantity_unit"] == "minutes"
    assert item["billing_source"] == "virtual_runtime_credits"


def test_connector_read_usage_maps_to_read_line_item() -> None:
    item = credit_ledger_contract.build_credit_ledger_line_item(
        metadata={"credit_item_type": "connector_read", "read_count": 3, "connector_kind": "google_sheets"},
        billing_source="empyralis_credits",
    )

    assert item["credit_item_type"] == "connector_read"
    assert item["credit_type"] == "connector_read"
    assert item["quantity"] == 3.0
    assert item["quantity_unit"] == "reads"
    assert item["connector_kind"] == "google_sheets"


def test_generic_tool_execution_maps_to_tool_line_item() -> None:
    item = credit_ledger_contract.build_credit_ledger_line_item(
        metadata={"credit_item_type": "tool_execution", "action_count": 4, "tool_id": "browser.click"},
        billing_source="empyralis_credits",
    )

    assert item["credit_item_type"] == "tool_execution"
    assert item["credit_type"] == "tool_execution"
    assert item["quantity"] == 4.0
    assert item["quantity_unit"] == "actions"
    assert item["tool_id"] == "browser.click"


def test_mini_app_action_usage_maps_to_action_line_item() -> None:
    item = credit_ledger_contract.build_credit_ledger_line_item(
        metadata={"credit_item_type": "mini_app_action", "action_count": 2, "app_id": "flashcards"},
        billing_source="empyralis_credits",
    )

    assert item["credit_item_type"] == "mini_app_action"
    assert item["credit_type"] == "mini_app_action"
    assert item["quantity"] == 2.0
    assert item["quantity_unit"] == "actions"
    assert item["app_id"] == "flashcards"


def test_unknown_hosted_tier_defaults_to_ai_pro_tokens() -> None:
    item = credit_ledger_contract.build_credit_ledger_line_item(
        metadata={},
        total_tokens=321,
    )

    assert item["credit_item_type"] == "ai_pro_tokens"
    assert item["credit_multiplier"] == 1.0
    assert item["quantity"] == 321.0


def test_pro_line_item_preserves_internal_route_metadata() -> None:
    item = credit_ledger_contract.build_credit_ledger_line_item(
        metadata={
            "public_tier": "pro",
            "effective_provider": "deepseek",
            "effective_model": "deepseek-v4-pro",
            "fallback_provider": "deepseek",
            "fallback_model": "deepseek-v4-flash",
        },
        total_tokens=777,
    )

    assert item["public_tier"] == "pro"
    assert item["credit_item_type"] == "ai_pro_tokens"
    assert item["effective_provider"] == "deepseek"
    assert item["effective_model"] == "deepseek-v4-pro"
    assert item["fallback_provider"] == "deepseek"
    assert item["fallback_model"] == "deepseek-v4-flash"


def test_unified_ledger_event_carries_measurement_dimensions() -> None:
    event = credit_ledger_contract.build_unified_credit_ledger_event(
        surface="studio",
        source_surface="deployed_agent_channel",
        payer="BYOK",
        credit_type="ai_tokens",
        provider="openrouter",
        model="openai/gpt-5.2",
        runtime_target="cloud_default",
        workspace_id="ws-1",
        user_id="user-1",
        thread_id="thread-1",
        run_id="run-1",
        agent_id="agent-1",
        provider_usage={"prompt_tokens": 10, "completion_tokens": 5},
        platform_cost_usd=0,
        provider_reported_cost=0.0003,
        provider_reported_currency="USD",
        credits_debited=0,
        estimation_mode="provider_usage_exact",
        created_at="2026-05-21T00:00:00Z",
    )

    assert event["surface"] == "studio"
    assert event["source_surface"] == "deployed_agent_channel"
    assert event["payer"] == "BYOK"
    assert event["credit_type"] == "ai_tokens"
    assert event["provider_reported_cost"] == 0.0003
    assert event["credits_debited"] == 0.0
    assert event["provider_usage"]["prompt_tokens"] == 10


def test_unified_ledger_event_accepts_knowledge_retrieval_transparency_rows() -> None:
    event = credit_ledger_contract.build_unified_credit_ledger_event(
        surface="studio",
        source_surface="studio_knowledge_verify",
        payer="local",
        credit_type="knowledge_retrieval",
        workspace_id="ws-1",
        agent_id="agent-1",
        provider_usage={"retrieved_chunk_count": 2, "indexed_chunk_count": 12},
        platform_cost_usd=0,
        credits_debited=0,
        estimation_mode="local_retrieval",
    )

    assert event["credit_type"] == "knowledge_retrieval"
    assert event["payer"] == "local"
    assert event["credits_debited"] == 0.0
    assert event["provider_usage"]["retrieved_chunk_count"] == 2


def test_unified_ledger_event_rejects_unknown_surface() -> None:
    try:
        credit_ledger_contract.build_unified_credit_ledger_event(
            surface="runtime",
            payer="platform_credits",
            credit_type="ai_tokens",
        )
    except ValueError as exc:
        assert "surface" in str(exc)
    else:
        raise AssertionError("expected invalid surface to fail")

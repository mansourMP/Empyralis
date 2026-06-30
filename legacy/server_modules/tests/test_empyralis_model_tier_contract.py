from server_modules import empyralis_model_tier_contract as contract


def test_contract_exposes_exact_public_tiers_in_order():
    assert contract.EMPYRALIS_MODEL_TIERS == (
        "light",
        "pro",
        "local_ai",
        "my_api_key",
        "my_ai_account",
    )
    assert [item["public_label"] for item in contract.ordinary_ui_model_tier_contracts()] == [
        "Light",
        "Pro",
        "Local AI",
        "My API Key",
        "My AI Account",
    ]


def test_empyralis_hosted_tiers_hide_internal_provider_model_from_ordinary_ui():
    ordinary_items = {
        item["public_tier"]: item
        for item in contract.ordinary_ui_model_tier_contracts()
    }
    admin_items = {
        item["public_tier"]: item
        for item in contract.admin_model_tier_contracts()
    }

    for tier in contract.EMPYRALIS_HOSTED_TIERS:
        assert ordinary_items[tier]["billing_source"] == "empyralis_credits"
        assert ordinary_items[tier]["user_owned"] is False
        assert ordinary_items[tier]["expose_provider_model_to_ordinary_ui"] is False
        assert ordinary_items[tier]["internal_provider"] is None
        assert ordinary_items[tier]["internal_model"] is None
        assert admin_items[tier]["internal_provider"]
        assert admin_items[tier]["internal_model"]


def test_empyralis_hosted_tiers_use_deepseek_v4_with_distinct_runtime_contracts():
    admin_items = {
        item["public_tier"]: item
        for item in contract.admin_model_tier_contracts()
    }

    assert admin_items["light"]["internal_provider"] == "deepseek"
    assert admin_items["light"]["internal_model"] == "deepseek-chat"
    assert admin_items["light"]["thinking_mode"] == "off"
    assert admin_items["light"]["agent_budget_tier"] == "standard"

    assert admin_items["pro"]["internal_provider"] == "deepseek"
    assert admin_items["pro"]["internal_model"] == "deepseek-v4-pro"
    assert admin_items["pro"]["reasoning_effort"] == "high"
    assert admin_items["pro"]["thinking_mode"] == "high"
    assert admin_items["pro"]["agent_budget_tier"] == "expanded"

    assert admin_items["light"]["credit_multiplier"] < admin_items["pro"]["credit_multiplier"]


def test_thinking_mode_is_derived_from_reasoning_effort():
    light = contract.model_tier_contract("light")
    pro = contract.model_tier_contract("pro")
    local = contract.model_tier_contract("local_ai")

    assert light.reasoning_effort is None
    assert light.thinking_mode == "off"
    assert pro.reasoning_effort == "high"
    assert pro.thinking_mode == "high"
    assert local.reasoning_effort is None
    assert local.thinking_mode is None


def test_user_owned_tiers_allow_provider_model_picker_in_ordinary_ui():
    ordinary_items = {
        item["public_tier"]: item
        for item in contract.ordinary_ui_model_tier_contracts()
    }

    assert ordinary_items["local_ai"]["billing_source"] == "local_runtime"
    assert ordinary_items["my_api_key"]["billing_source"] == "user_api_key"
    assert ordinary_items["my_ai_account"]["billing_source"] == "user_ai_account"

    for tier in contract.USER_OWNED_TIERS:
        assert ordinary_items[tier]["user_owned"] is True
        assert ordinary_items[tier]["expose_provider_model_to_ordinary_ui"] is True
        assert contract.user_owned_tier_allows_provider_picker(tier) is True


def test_normalize_model_tier_is_stable_and_defaults_to_pro():
    assert contract.normalize_model_tier("local-ai") == "local_ai"
    assert contract.normalize_model_tier("MY_API_KEY") == "my_api_key"
    assert contract.normalize_model_tier("max") == "pro"
    assert contract.normalize_model_tier("unknown") == "pro"
    assert contract.model_tier_contract("unknown").public_tier == "pro"


def test_fallbacks_never_point_to_user_owned_tiers():
    for tier in contract.EMPYRALIS_HOSTED_TIERS:
        fallback = contract.model_tier_contract(tier).fallback_tier
        assert fallback is None or fallback in contract.EMPYRALIS_HOSTED_TIERS

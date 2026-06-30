from server_modules import marketplace_distribution_service
from server_modules import studio_proof_agent_seed_service


REQUIRED_CONTRACT_KEYS = {
    "slug",
    "name",
    "persona",
    "default_data_sources",
    "channels",
    "tools_skills",
    "runtime_tier_recommendation",
    "approval_policy",
    "analytics_events",
    "monetization_hint",
    "customization",
}


def test_lists_exact_three_roi_proof_agent_seed_contracts():
    contracts = studio_proof_agent_seed_service.list_studio_proof_agent_seed_contracts()

    assert [item["slug"] for item in contracts] == [
        "shop-assistant",
        "dental-receptionist",
        "restaurant-order-taker",
    ]
    assert {item["name"] for item in contracts} == {
        "Shop Assistant",
        "Dental Receptionist",
        "Restaurant Order Taker",
    }


def test_each_proof_agent_contract_is_complete_and_customizable():
    for contract in studio_proof_agent_seed_service.list_studio_proof_agent_seed_contracts():
        assert REQUIRED_CONTRACT_KEYS.issubset(contract.keys())
        assert contract["persona"]["editable"] is True
        assert contract["default_data_sources"]
        assert contract["channels"]
        assert contract["tools_skills"]
        assert contract["runtime_tier_recommendation"]["tier"] in {"hosted_secure", "local_secure", "privileged_device"}
        assert contract["approval_policy"]["owner_approval_required_for"]
        assert contract["analytics_events"]
        assert contract["monetization_hint"]["metric"]

        customization = contract["customization"]
        assert customization["persona_editable"] is True
        assert customization["data_sources_extensible"] is True
        assert customization["channels_extensible"] is True
        assert customization["tools_skills_extensible"] is True
        assert customization["additional_fields_allowed"] is True
        assert customization["template_inputs_schema"]["additionalProperties"] is True


def test_contracts_are_returned_as_defensive_copies():
    first = studio_proof_agent_seed_service.get_studio_proof_agent_seed_contract("shop-assistant")
    assert first is not None
    first["persona"]["default_name"] = "Mutated"
    first["default_data_sources"].append({"source_id": "mutated"})

    second = studio_proof_agent_seed_service.get_studio_proof_agent_seed_contract("shop-assistant")

    assert second is not None
    assert second["persona"]["default_name"] == "Shop Assistant"
    assert all(item.get("source_id") != "mutated" for item in second["default_data_sources"])


def test_shop_assistant_contract_includes_revenue_proof_assets():
    contract = studio_proof_agent_seed_service.get_studio_proof_agent_seed_contract("shop-assistant")
    assert contract is not None
    revenue_proof = contract.get("revenue_proof")
    assert isinstance(revenue_proof, dict)
    assert revenue_proof["vertical"] == "shop_assistant"
    assert revenue_proof["product_catalog_schema"]["required_columns"][0]["key"] == "sku"
    assert len(revenue_proof["demo_catalog_rows"]) >= 3
    assert len(revenue_proof["golden_conversations"]) >= 4
    assert any(item["event"] == "studio.proof.shop_assistant.intent_captured" for item in revenue_proof["activity_events"])
    assert "results" in revenue_proof["roi_snapshot"]
    assert "mass_messages" in revenue_proof["owner_approval_policy"]["owner_approval_required_for"]
    tool_ids = {item["id"] for item in contract["tools_skills"]}
    assert {"catalog.lookup", "pricing.quote", "order.status_lookup"}.issubset(tool_ids)


def test_shop_assistant_roi_snapshot_uses_event_counts_and_assumptions():
    snapshot = studio_proof_agent_seed_service.build_shop_assistant_roi_snapshot(
        event_counts={
            "conversations_handled": 100,
            "qualified_purchase_intent": 20,
            "owner_handoffs": 4,
        },
        assumptions={
            "avg_order_value_usd": 80.0,
            "close_rate_from_intent": 0.4,
            "gross_margin_rate": 0.5,
            "monthly_subscription_cost_usd": 100.0,
        },
    )

    assert snapshot["event_counts"]["qualified_purchase_intent"] == 20
    assert snapshot["results"]["estimated_orders"] == 8.0
    assert snapshot["results"]["estimated_revenue_usd"] == 640.0
    assert snapshot["results"]["estimated_gross_profit_usd"] == 320.0
    assert snapshot["results"]["estimated_net_value_usd"] == 220.0
    assert snapshot["results"]["roi_multiple"] == 3.2


def test_marketplace_package_projection_is_normalizable_agent_template_contract():
    packages = studio_proof_agent_seed_service.build_studio_proof_agent_marketplace_package_contracts()

    assert len(packages) == 3
    for package in packages:
        normalized = marketplace_distribution_service._normalize_package_payload(package)

        assert normalized["kind"] == "agent_template"
        assert normalized["install_target"] == "template_catalog"
        assert normalized["review_state"] == "approved"
        assert normalized["policy_posture"] == "governed"
        assert normalized["agent_template"]["template_id"] in {
            "shop-assistant",
            "dental-receptionist",
            "restaurant-order-taker",
        }
        assert normalized["agent_template"]["launch_checklist"]
        assert "proof_agent_seed_contract" in normalized["agent_template"]["context_envelope"]
        assert normalized["agent_template"]["context_envelope"]["proof_agent_seed_contract"]["customization"]["additional_fields_allowed"] is True

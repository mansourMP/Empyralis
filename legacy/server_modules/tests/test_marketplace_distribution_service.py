import asyncio
import json

import pytest

from server_modules import app_registry_api
from server_modules import marketplace_distribution_service
from server_modules import mini_apps_service
from server_modules import provider_catalog_service
from server_modules import workspace_context


INSTALLABLE_PROOF_PACKAGES = {
    "studio-proof-shop-assistant": "shop-assistant",
    "studio-proof-restaurant-order-taker": "restaurant-order-taker",
    "studio-proof-dental-receptionist": "dental-receptionist",
}
CURATED_LINK_APPS = {"link-instagram", "link-slack", "link-telegram"}


def _patch_app_registry(monkeypatch, tmp_path):
    registry_path = tmp_path / "apps.json"

    def _safe_read_json(path, fallback):
        if not path.exists():
            return fallback
        return json.loads(path.read_text(encoding="utf-8"))

    def _safe_write_json(path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    monkeypatch.setattr(app_registry_api, "ORION_APP_REGISTRY_FILE", registry_path, raising=False)
    monkeypatch.setattr(app_registry_api, "_safe_read_json", _safe_read_json, raising=False)
    monkeypatch.setattr(app_registry_api, "_safe_write_json", _safe_write_json, raising=False)
    monkeypatch.setattr(app_registry_api, "_utc_now_iso", lambda: "2026-04-22T00:00:00Z", raising=False)


def test_empty_marketplace_returns_preview_packages(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace_context, "_WORKSPACE_DIR", tmp_path / "workspace")

    payload = marketplace_distribution_service.list_marketplace_packages("ws-empty")
    template_payload = marketplace_distribution_service.list_marketplace_packages("ws-empty", kind="agent_template")

    assert payload["count"] >= 9
    package_ids = {item["package_id"] for item in payload["items"]}
    assert "preview-restaurant-orders" in package_ids
    assert "preview-deepseek-provider" not in package_ids
    assert all(item["kind"] != "provider" for item in payload["items"])
    assert set(INSTALLABLE_PROOF_PACKAGES).issubset(package_ids)
    assert CURATED_LINK_APPS.issubset(package_ids)
    installable_items = [
        item for item in payload["items"] if item["package_id"] in INSTALLABLE_PROOF_PACKAGES
    ]
    assert {item["package_id"] for item in installable_items} == set(INSTALLABLE_PROOF_PACKAGES)
    for item in installable_items:
        assert item["preview_only"] is False
        assert item["install_eligible"] is True
        assert item["install_blockers"] == []
        assert item["install_target"] == "template_catalog"
        assert item["runtime_truth"]["surface"] == "template_catalog"
        assert item["marketplace_proof"]["golden_tests"]
        assert item["marketplace_proof"]["analytics"]["business_metrics"]["dashboard_cards"]
        contract = item["package"]["context_envelope"]["proof_agent_seed_contract"]
        assert contract["monetization_hint"]["suggested_offer"]
        assert contract["analytics_events"]
        assert contract["business_metrics"]["dashboard_cards"]
    link_items = [item for item in payload["items"] if item["package_id"] in CURATED_LINK_APPS]
    assert {item["runtime_truth"]["runtime_type"] for item in link_items} == {"link"}
    assert all(item["install_eligible"] is True for item in link_items)
    shop_assistant = next(item for item in payload["items"] if item["package_id"] == "studio-proof-shop-assistant")
    assert shop_assistant["marketplace_proof"]["vertical"] == "shop_assistant"
    assert "answers_catalog_question_without_inventory_hallucination" in shop_assistant["marketplace_proof"]["golden_tests"]
    assert shop_assistant["package"]["template_id"] == "shop-assistant"
    preview_packages = [
        item for item in payload["items"]
        if item["package_id"] not in INSTALLABLE_PROOF_PACKAGES and item["package_id"] not in CURATED_LINK_APPS
    ]
    assert all(item["preview_only"] is True for item in preview_packages)
    assert all(item["install_eligible"] is False for item in preview_packages)
    assert all("preview_only" in item["install_blockers"] for item in preview_packages)
    assert template_payload["count"] == 6
    assert template_payload["items"][0]["kind"] == "agent_template"


@pytest.mark.parametrize(("package_id", "slug"), sorted(INSTALLABLE_PROOF_PACKAGES.items()))
def test_installable_proof_agent_seed_can_be_installed(monkeypatch, tmp_path, package_id, slug):
    monkeypatch.setattr(workspace_context, "_WORKSPACE_DIR", tmp_path / "workspace")

    state = marketplace_distribution_service._safe_read_state("ws-empty")
    state["packages"] = marketplace_distribution_service._preview_marketplace_packages()
    marketplace_distribution_service._save_state("ws-empty", state)

    installed = marketplace_distribution_service.install_marketplace_package(
        "ws-empty",
        package_id=package_id,
        actor_user_id="user-1",
    )

    assert installed["installed"] is True
    assert installed["preview_only"] is False
    assert installed["install"]["status"] == "installed"
    assert installed["runtime_truth"]["surface"] == "template_catalog"
    assert installed["runtime_truth"]["open_href"] == f"/w/ws-empty/studio?proof_agent={slug}"
    assert installed["analytics"]["install_count"] == 1


def test_install_marketplace_app_syncs_app_registry_and_hosted_contract(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace_context, "_WORKSPACE_DIR", tmp_path / "workspace")
    _patch_app_registry(monkeypatch, tmp_path)

    registered = marketplace_distribution_service.register_marketplace_package(
        "ws-1",
        actor_user_id="user-1",
        payload={
            "package_id": "weather-lab.console",
            "kind": "app",
            "label": "Weather Lab Console",
            "description": "Third-party weather intelligence app for the Empyralis shell.",
            "category": "Research",
            "verification_status": "unverified",
            "review_state": "approved",
            "health_state": "healthy",
            "publisher": {
                "publisher_id": "weather-lab",
                "label": "Weather Lab",
                "website": "https://weather.example",
                "support_url": "https://weather.example/support",
            },
            "app": {
                "app_id": "weather_console",
                "hosted_url": "https://apps.weather.example/embed",
                "icon_url": "https://apps.weather.example/icon.png",
                "runtime_type": "community",
                "allowed_origins": ["https://apps.weather.example"],
                "permissions": ["app.summary.read", "app.bridge.sage.request"],
                "bridge_contracts": {"app_to_sage": ["summary_request"]},
                "context_envelope": {"user_selected_inputs": ["location"]},
            },
        },
    )

    assert registered["package_id"] == "weather-lab.console"
    listed = marketplace_distribution_service.list_marketplace_packages("ws-1")
    assert listed["count"] == 1
    assert listed["items"][0]["preview_only"] is False
    installed = marketplace_distribution_service.install_marketplace_package(
        "ws-1",
        package_id="weather-lab.console",
        actor_user_id="user-1",
    )

    assert installed["installed"] is True
    assert installed["runtime_truth"]["surface"] == "app_registry"
    assert installed["runtime_truth"]["runtime_type"] == "community"
    assert installed["runtime_truth"]["open_href"] == "/w/ws-1/applications/weather_console"

    with app_registry_api.APP_REGISTRY_LOCK:
        data = app_registry_api._load_app_registry()
        app_item = app_registry_api._find_app(data, "weather_console")
    assert app_item is not None
    assert app_item["status"] == "installed"
    assert app_item["source"] == "third_party_marketplace"
    assert app_item["install_source"] == "marketplace_distribution"
    assert app_item["icon_url"] == "https://apps.weather.example/icon.png"
    assert app_item["runtime_type"] == "community"
    assert app_item["distribution_metadata"]["verification_status"] == "unverified"

    hosted_contract = mini_apps_service.get_mini_app_contract("ws-1", "weather_console")
    assert hosted_contract["hosted_url"] == "https://apps.weather.example/embed"
    assert hosted_contract["delivery_mode"] == "hosted"
    assert hosted_contract["icon_url"] == "https://apps.weather.example/icon.png"
    assert hosted_contract["runtime_type"] == "community"
    assert "app.summary.read" in hosted_contract["permissions"]
    assert "app.bridge.sage.request" in hosted_contract["permissions"]


def test_public_marketplace_app_without_icon_is_blocked(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace_context, "_WORKSPACE_DIR", tmp_path / "workspace")
    _patch_app_registry(monkeypatch, tmp_path)

    registered = marketplace_distribution_service.register_marketplace_package(
        "ws-1",
        actor_user_id="reviewer-1",
        payload={
            "package_id": "no-icon-app",
            "kind": "app",
            "label": "No Icon App",
            "description": "Public app missing required marketplace icon.",
            "verification_status": "partner",
            "review_state": "approved",
            "policy_posture": "governed",
            "onboarding": {
                "privacy_url": "https://apps.example/privacy",
                "support_url": "https://apps.example/support",
            },
            "app": {
                "app_id": "no_icon_app",
                "hosted_url": "https://apps.example/no-icon",
                "runtime_type": "community",
                "allowed_origins": ["https://apps.example"],
            },
        },
    )

    assert "missing_app_icon" in registered["marketplace_review_findings"]
    assert registered["install_eligible"] is False
    assert "missing_app_icon" in registered["install_blockers"]

    with pytest.raises(ValueError, match="missing_app_icon"):
        marketplace_distribution_service.install_marketplace_package(
            "ws-1",
            package_id="no-icon-app",
            actor_user_id="user-1",
        )


def test_private_workspace_app_without_icon_is_accepted(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace_context, "_WORKSPACE_DIR", tmp_path / "workspace")

    contract = mini_apps_service.upsert_mini_app_contract(
        "ws-1",
        "private_customer_portal",
        label="Private Customer Portal",
        delivery_mode="hosted",
        hosted_url="https://apps.example/private",
        allowed_origins=["https://apps.example"],
        trust_tier="user_private",
        runtime_type="private",
        permissions=[],
        bridge_contracts={},
    )

    assert contract["app_id"] == "private_customer_portal"
    assert contract["delivery_mode"] == "hosted"
    assert contract["icon_url"] is None
    assert contract["runtime_type"] == "private"


def test_link_app_installs_and_opens_destination_url(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace_context, "_WORKSPACE_DIR", tmp_path / "workspace")
    _patch_app_registry(monkeypatch, tmp_path)

    registered = marketplace_distribution_service.register_marketplace_package(
        "ws-1",
        actor_user_id="reviewer-1",
        payload={
            "package_id": "telegram",
            "kind": "app",
            "label": "Telegram",
            "description": "Curated link app for Telegram Web.",
            "verification_status": "unverified",
            "review_state": "approved",
            "health_state": "healthy",
            "policy_posture": "governed",
            "app": {
                "app_id": "telegram_link",
                "runtime_type": "link",
                "destination_url": "https://web.telegram.org/",
                "icon_url": "https://telegram.org/img/t_logo.png",
                "allowed_origins": ["https://web.telegram.org"],
            },
        },
    )

    assert registered["install_eligible"] is True
    installed = marketplace_distribution_service.install_marketplace_package(
        "ws-1",
        package_id="telegram",
        actor_user_id="user-1",
    )
    assert installed["runtime_truth"]["runtime_type"] == "link"
    assert installed["runtime_truth"]["open_href"] == "https://web.telegram.org/"
    contract = mini_apps_service.get_mini_app_contract("ws-1", "telegram_link")
    assert contract["runtime_type"] == "link"
    assert contract["destination_url"] == "https://web.telegram.org/"


def test_community_submission_enters_pending_queue_and_review_publishes(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace_context, "_WORKSPACE_DIR", tmp_path / "workspace")
    _patch_app_registry(monkeypatch, tmp_path)

    submitted = marketplace_distribution_service.submit_community_app(
        "ws-1",
        actor_user_id="developer-1",
        payload={
            "label": "Community Notes",
            "description": "Hosted notes surface for workspace teams.",
            "category": "Productivity",
            "publisher": {
                "publisher_id": "community-dev",
                "label": "Community Dev",
            },
            "app": {
                "app_id": "community_notes",
                "hosted_url": "https://notes.example/app",
                "icon_url": "https://notes.example/icon.png",
                "allowed_origins": ["https://notes.example"],
            },
        },
    )

    assert submitted["review_state"] == "pending"
    assert submitted["runtime_truth"]["runtime_type"] == "community"
    assert marketplace_distribution_service.list_marketplace_packages("ws-1", kind="app")["count"] == 0
    queue = marketplace_distribution_service.list_marketplace_app_submissions("ws-1")
    assert queue["count"] == 1
    assert queue["items"][0]["package_id"] == "community_notes"

    approved = marketplace_distribution_service.review_marketplace_app_submission(
        "ws-1",
        package_id="community_notes",
        actor_user_id="owner-1",
        approved=True,
        verification_status="partner",
    )

    assert approved["review_state"] == "approved"
    assert approved["verification_status"] == "partner"
    listed = marketplace_distribution_service.list_marketplace_packages("ws-1", kind="app", runtime_type="community")
    assert listed["count"] == 1
    assert listed["items"][0]["package_id"] == "community_notes"


def test_marketplace_install_blocks_owner_resource_permissions(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace_context, "_WORKSPACE_DIR", tmp_path / "workspace")
    _patch_app_registry(monkeypatch, tmp_path)

    registered = marketplace_distribution_service.register_marketplace_package(
        "ws-1",
        actor_user_id="user-1",
        payload={
            "package_id": "unsafe-owner-resource-app",
            "kind": "app",
            "label": "Unsafe Owner Resource App",
            "description": "Attempts to request owner private runtime access.",
            "verification_status": "verified",
            "review_state": "approved",
            "health_state": "healthy",
            "policy_posture": "governed",
            "onboarding": {
                "privacy_url": "https://apps.example.com/privacy",
                "support_url": "https://apps.example.com/support",
            },
            "app": {
                "app_id": "unsafe_owner_resource_app",
                "hosted_url": "https://apps.example.com/unsafe",
                "icon_url": "https://apps.example.com/unsafe/icon.png",
                "allowed_origins": ["https://apps.example.com"],
                "permissions": ["app.summary.read", "read_sage_memory", "connected_computer"],
            },
        },
    )

    assert "owner_resource_boundary_violation" in registered["marketplace_review_findings"]
    listed = marketplace_distribution_service.get_marketplace_package("ws-1", "unsafe-owner-resource-app")
    assert listed["install_eligible"] is False
    assert "owner_resource_boundary_violation" in listed["install_blockers"]

    with pytest.raises(ValueError) as exc_info:
        marketplace_distribution_service.install_marketplace_package(
            "ws-1",
            package_id="unsafe-owner-resource-app",
            actor_user_id="user-1",
        )

    assert "owner_resource_boundary_violation" in str(exc_info.value)


def test_install_marketplace_provider_projects_into_provider_catalog(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace_context, "_WORKSPACE_DIR", tmp_path / "workspace")
    _patch_app_registry(monkeypatch, tmp_path)

    marketplace_distribution_service.register_marketplace_package(
        "ws-1",
        actor_user_id="user-1",
        payload={
            "package_id": "neural-cloud.models",
            "kind": "provider",
            "label": "Neural Cloud Models",
            "description": "Third-party reasoning and retrieval models distributed through Empyralis.",
            "category": "Models",
            "verification_status": "verified",
            "review_state": "approved",
            "health_state": "healthy",
            "publisher": {
                "publisher_id": "neural-cloud",
                "label": "Neural Cloud",
            },
            "billing": {
                "monetization_kind": "metered",
                "billing_product_id": "nc-metered",
                "revenue_share_bps": 1200,
                "accounting_hook": {"ledger_key": "neural-cloud.models.usage"},
            },
            "provider": {
                "provider_id": "neuralcloud",
                "default_model": "neuralcloud-reasoner",
                "auth_modes": ["api_key"],
                "privacy_posture": "Vendor-hosted third-party reasoning API.",
                "jurisdiction": "US",
                "residency": "Vendor-managed",
                "models": [
                    {
                        "id": "neuralcloud-reasoner",
                        "label": "Neural Cloud Reasoner",
                        "input_cost_per_1k_usd": 0.004,
                        "output_cost_per_1k_usd": 0.012,
                        "supports_tools": True,
                        "supports_reasoning": True,
                        "reasoning_levels": ["medium", "high"],
                        "capability_labels": ["Reasoning", "Hosted API"],
                    }
                ],
            },
        },
    )
    installed = marketplace_distribution_service.install_marketplace_package(
        "ws-1",
        package_id="neural-cloud.models",
        actor_user_id="user-1",
    )
    assert installed["installed"] is True

    runtime_event = marketplace_distribution_service.record_marketplace_runtime_event(
        "ws-1",
        package_id="neural-cloud.models",
        event_type="runtime_ok",
        health_state="healthy",
        metadata={"probe": "catalog_smoke"},
    )
    assert runtime_event["analytics"]["runtime_event_count"] == 1

    monkeypatch.setattr(
        provider_catalog_service.provider_profiles,
        "build_workspace_provider_connection_truth",
        lambda workspace_id: {
            "workspace_id": workspace_id,
            "summary": {"provider_total": 0},
            "providers": [],
        },
    )
    monkeypatch.setattr(
        provider_catalog_service.provider_profiles,
        "build_provider_runtime_truth",
        lambda workspace_id: {
            "workspace_id": workspace_id,
            "summary": {"available_count": 0, "configured_count": 0},
            "providers": [],
        },
    )

    payload = asyncio.run(provider_catalog_service.list_workspace_provider_catalog(workspace_id="ws-1"))
    provider = next(item for item in payload["providers"] if item["id"] == "neuralcloud")

    assert provider["distribution_origin"] == "third_party_marketplace"
    assert provider["verification_status"] == "verified"
    assert provider["review_state"] == "approved"
    assert provider["billing_hooks"]["revenue_share_bps"] == 1200
    assert provider["analytics"]["runtime_event_count"] == 1
    assert provider["models"][0]["id"] == "neuralcloud-reasoner"


def test_governed_package_contracts_cover_templates_connectors_skills_and_mini_apps(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace_context, "_WORKSPACE_DIR", tmp_path / "workspace")

    packages = [
        {
            "kind": "agent_template",
            "label": "Appointment Booking Template",
            "description": "Governed specialist template for appointment booking.",
            "verification_status": "verified",
            "review_state": "approved",
            "policy_posture": "governed",
            "billing": {
                "monetization_kind": "revenue_share",
                "revenue_share_bps": 1500,
                "accounting_hook": {"ledger_key": "template.appointment_booking.install"},
            },
            "agent_template": {
                "template_id": "appointment_booking",
                "required_connectors": ["calendar"],
                "suggested_tools": ["availability_lookup"],
                "launch_checklist": ["Configure calendar", "Review handoff policy"],
            },
        },
        {
            "kind": "connector",
            "label": "Calendar Connector",
            "description": "Governed calendar connector contract.",
            "verification_status": "partner",
            "review_state": "approved",
            "policy_posture": "governed",
            "billing": {
                "monetization_kind": "metered",
                "accounting_hook": {"ledger_key": "connector.calendar.usage"},
            },
            "connector": {
                "connector_id": "calendar",
                "connector_class": "api_connector",
                "auth_modes": ["oauth"],
                "scopes": ["calendar.read", "calendar.write"],
                "actions": ["availability.lookup", "booking.create"],
            },
        },
        {
            "kind": "skill",
            "label": "FAQ Answering Skill",
            "description": "Governed retrieval skill contract.",
            "verification_status": "verified",
            "review_state": "approved",
            "policy_posture": "governed",
            "billing": {
                "monetization_kind": "subscription",
                "billing_product_id": "faq-skill-pro",
                "accounting_hook": {"ledger_key": "skill.faq.install"},
            },
            "skill": {
                "skill_id": "faq_answering",
                "runtime": "hosted",
                "permissions": ["knowledge:read"],
                "tool_contracts": {"knowledge": ["retrieve"]},
            },
        },
        {
            "kind": "mini_app",
            "label": "Booking Board",
            "description": "Governed hosted mini-app package.",
            "verification_status": "verified",
            "review_state": "approved",
            "policy_posture": "governed",
            "onboarding": {
                "privacy_url": "https://apps.example/privacy",
                "support_url": "https://apps.example/support",
            },
            "billing": {
                "monetization_kind": "free",
                "accounting_hook": {"ledger_key": "mini_app.booking_board.install"},
            },
            "mini_app": {
                "app_id": "booking_board",
                "hosted_url": "https://apps.example/booking-board",
                "icon_url": "https://apps.example/booking-board/icon.png",
                "runtime_type": "community",
                "allowed_origins": ["https://apps.example"],
                "permissions": ["app.summary.read"],
                "bridge_contracts": {"bookings": ["read"]},
            },
        },
    ]

    package_ids = [
        marketplace_distribution_service.register_marketplace_package(
            "ws-1",
            actor_user_id="reviewer-1",
            payload=payload,
        )["package_id"]
        for payload in packages
    ]

    listed = marketplace_distribution_service.list_marketplace_packages("ws-1")
    listed_by_id = {item["package_id"]: item for item in listed["items"]}
    assert {listed_by_id[package_id]["kind"] for package_id in package_ids} == {
        "agent_template",
        "connector",
        "skill",
        "mini_app",
    }
    assert all(listed_by_id[package_id]["install_eligible"] is True for package_id in package_ids)
    assert listed_by_id["appointment_booking"]["billing"]["revenue_share_bps"] == 1500
    assert listed_by_id["calendar"]["runtime_truth"]["surface"] == "connector_catalog"
    assert listed_by_id["faq_answering"]["package"]["tool_contracts"] == {"knowledge": ["retrieve"]}

    installed_mini_app = marketplace_distribution_service.install_marketplace_package(
        "ws-1",
        package_id="booking_board",
        actor_user_id="user-1",
    )
    assert installed_mini_app["runtime_truth"]["surface"] == "mini_app_registry"
    assert installed_mini_app["runtime_truth"]["runtime_type"] == "community"
    assert installed_mini_app["runtime_truth"]["open_href"] == "/w/ws-1/applications/booking_board"
    hosted_contract = mini_apps_service.get_mini_app_contract("ws-1", "booking_board")
    assert hosted_contract["hosted_url"] == "https://apps.example/booking-board"


def test_restricted_or_unverified_marketplace_packages_are_not_installable(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace_context, "_WORKSPACE_DIR", tmp_path / "workspace")

    restricted = marketplace_distribution_service.register_marketplace_package(
        "ws-1",
        actor_user_id="reviewer-1",
        payload={
            "kind": "skill",
            "label": "Unreviewed Plugin Skill",
            "description": "Should remain a governed contract, not an open plugin install lane.",
            "verification_status": "unverified",
            "review_state": "restricted",
            "policy_posture": "restricted",
            "billing": {"monetization_kind": "metered"},
            "skill": {"skill_id": "unreviewed_plugin", "permissions": ["shell:execute"]},
        },
    )

    assert restricted["install_eligible"] is False
    assert restricted["review_state"] == "restricted"
    assert restricted["policy_posture"] == "restricted"
    assert "review_not_approved" in restricted["install_blockers"]
    assert "verification_required" in restricted["install_blockers"]
    assert "policy_restricted" in restricted["install_blockers"]

    with pytest.raises(ValueError, match="not installable"):
        marketplace_distribution_service.install_marketplace_package(
            "ws-1",
            package_id="unreviewed_plugin",
            actor_user_id="user-1",
        )


def test_preview_packages_with_trust_metadata_remain_preview_only(monkeypatch, tmp_path):
    """Preview package IDs stay non-installable even with verified+approved+governed metadata."""
    monkeypatch.setattr(workspace_context, "_WORKSPACE_DIR", tmp_path / "workspace")
    _patch_app_registry(monkeypatch, tmp_path)

    state = marketplace_distribution_service._safe_read_state("ws-phase8")
    state["packages"] = marketplace_distribution_service._preview_marketplace_packages()
    marketplace_distribution_service._save_state("ws-phase8", state)

    listed = marketplace_distribution_service.list_marketplace_packages("ws-phase8")
    restaurant = next(item for item in listed["items"] if item["package_id"] == "preview-restaurant-orders")
    assert restaurant["preview_only"] is True
    assert restaurant["install_eligible"] is False
    assert "preview_only" in restaurant["install_blockers"]

    with pytest.raises(ValueError, match="preview_only"):
        marketplace_distribution_service.install_marketplace_package(
            "ws-phase8",
            package_id="preview-restaurant-orders",
            actor_user_id="user-1",
        )

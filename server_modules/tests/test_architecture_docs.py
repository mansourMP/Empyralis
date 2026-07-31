from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

OPEN_CORE_BOUNDARY_MAP = ROOT / "config" / "open_core_boundary.json"
PRODUCT_SURFACE_MAP = ROOT / "config" / "product_surface_map.json"
SURFACE_PARITY_MAP = ROOT / "config" / "surface_parity_contract_map.json"
CAPTAIN_SPECIALIST_MAP = ROOT / "config" / "captain_specialist_runtime_map.json"
APPLICATION_RUNTIME_MAP = ROOT / "config" / "application_runtime_contract_map.json"
ACTIVITY_TIMELINE_MAP = ROOT / "config" / "agent_activity_timeline_map.json"
LOCAL_RUNTIME_CLUSTER_MAP = ROOT / "config" / "local_runtime_cluster_map.json"
HYBRID_SYNC_POLICY_MAP = ROOT / "config" / "hybrid_sync_placement_map.json"

CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SECURITY_WORKFLOW = ROOT / ".github" / "workflows" / "security-baseline.yml"
SUPPLY_CHAIN_WORKFLOW = ROOT / ".github" / "workflows" / "supply-chain.yml"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"

FRONTEND_ACTIVITY_ROUTE = ROOT / "frontend" / "app" / "api" / "activity" / "timeline" / "route.ts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(_read(path))


# MAN-139 phase 2: nine doc-freeze tests deleted here
# (test_docs_lockdown_exposes_only_the_canonical_handoff_files,
# test_context_records_the_four_layer_model_and_surface_truth,
# test_bible_records_fail_closed_auth_and_public_webhook_rules,
# test_project_map_tracks_active_platform_roots_and_ci_truth,
# test_frontend_map_keeps_dumb_ui_and_contract_shared_surfaces,
# test_gateway_architecture_doc_freezes_local_gateway_boundary,
# test_gateway_protocol_doc_freezes_phase_one_message_family,
# test_personal_vs_studio_channel_doc_freezes_channel_boundary,
# test_pending_tasks_focus_on_execution_not_more_architecture_sprawl).
# All eleven asserted on the exact contents of docs/context.md, bible.md,
# project-map.md, frontend-map.md, pending-tasks.md,
# gateway-architecture.md, gateway-protocol.md, and
# personal-vs-studio-channel-model.md -- every one of which no longer
# exists. bb44160c0 ("chore: delete dead code -- mobile, apps/tray, docs/,
# python_engine, shared/, dead audits, gateway dist") deleted the entire
# docs/ directory ("60+ stale markdown documents"), and CLAUDE.md now
# states the policy directly: "Design/audit/gap/research documents are
# not kept. They were snapshots of a moment; they went stale within a
# week and agents cited them as present truth. Deleted 2026-07-31."
# Freezing the content of documents the product has explicitly decided
# not to keep is not a test that can be fixed -- there is nothing left to
# freeze. Findings now become Linear issues per that same policy.
def test_workflow_baseline_covers_backend_typecheck_and_supply_chain() -> None:
    ci = _read(CI_WORKFLOW)
    security = _read(SECURITY_WORKFLOW)
    supply_chain = _read(SUPPLY_CHAIN_WORKFLOW)
    release = _read(RELEASE_WORKFLOW)

    assert "Run demo-critical server test suite" in ci
    assert "python -m pytest \\" in ci
    assert "./node_modules/.bin/tsc --noEmit" in ci
    # ARCHIVED (Phase U1): supervisor build removed from CI.
    assert "actions/dependency-review-action@v4" in security
    # Was "gitleaks/gitleaks-action@v2" (the marketplace action) -- security-
    # baseline.yml now installs the gitleaks binary directly instead, but
    # still runs the same secret scan; updated to check for that, not
    # dropped, since the property this asserts (CI scans for secrets) still
    # holds.
    assert "Install gitleaks" in security
    assert "gitleaks dir" in security
    assert "pip-audit -r requirements.txt -r requirements-worker.txt" in security
    assert "anchore/sbom-action@v0" in supply_chain
    assert "actions/attest-build-provenance@v2" in supply_chain
    assert "tauri-apps/tauri-action@v1" in release
    assert "actions/attest-build-provenance@v2" in release


def test_open_core_boundary_map_classifies_distribution_boundaries() -> None:
    payload = _read_json(OPEN_CORE_BOUNDARY_MAP)
    allowed = {"open_source", "source_available", "managed_cloud_only", "enterprise_self_host_only"}
    component_map = {str(item["id"]): str(item["distribution"]) for item in payload["components"]}

    assert set(payload["distribution_classes"]) == allowed
    assert component_map["local_runtime_daemon"] == "open_source"
    assert component_map["desktop_app_shell"] == "open_source"
    assert component_map["mobile_clients"] == "source_available"
    assert component_map["hosted_control_plane"] == "managed_cloud_only"
    assert component_map["cloud_sage_runtime"] == "managed_cloud_only"
    assert component_map["private_control_plane_package"] == "enterprise_self_host_only"


def test_product_surface_map_preserves_shared_core_and_mobile_first_policy() -> None:
    """Was test_product_surface_map_and_mobile_tabs_align_with_surface_truth
    -- also read mobile/app/(tabs)/_layout.tsx and asserted its exact tab
    set, but bb44160c0 ("chore: delete dead code -- mobile, apps/tray,
    docs/, python_engine, shared/, dead audits, gateway dist") deleted
    mobile/ entirely ("mobile/ -- frozen Expo app"). config/
    product_surface_map.json itself is untouched and still real, live
    config, so those assertions stay; only the assertions against the
    now-nonexistent mobile layout file are gone."""
    payload = _read_json(PRODUCT_SURFACE_MAP)

    assert payload["shared_core"]["sage_identity"] == "shared"
    assert payload["shared_core"]["workspace_model"] == "shared"
    assert payload["shared_core"]["memory_system"] == "shared"
    assert payload["shared_core"]["specialist_installs"] == "shared"
    assert payload["shared_core"]["runtime_attachments"] == "shared"
    assert payload["mobile_first"]["daily_use_default"] is True
    assert payload["mobile_first"]["bottom_tabs"] == [
        "Chat",
        "Build",
        "Activity",
        "Settings",
    ]


def test_surface_parity_contract_map_preserves_same_platform_no_downgrade_rule() -> None:
    payload = _read_json(SURFACE_PARITY_MAP)

    shared_contract = payload["shared_platform_contract"]
    assert shared_contract["platform"] == "same"
    assert shared_contract["engine"] == "same"
    assert shared_contract["sage"] == "same"
    assert shared_contract["specialists"] == "same"
    assert shared_contract["runtime_placement_model"] == "same"
    assert payload["capability_determinants"] == [
        "runtime_availability",
        "policy",
        "connector_scope",
        "memory_scope",
        "approval_state",
    ]
    assert "ui_origin_based_capability_downgrades" in payload["forbidden_surface_divergence"]
    assert "runtime_attachment_management" in payload["desktop_only_depth"]


def test_runtime_boundary_maps_preserve_captain_specialist_and_app_separation() -> None:
    captain_specialist = _read_json(CAPTAIN_SPECIALIST_MAP)
    application_runtime = _read_json(APPLICATION_RUNTIME_MAP)

    layers = captain_specialist["layers"]
    assert layers["personal_captain"]["role"] == "private_main_agent"
    assert layers["personal_captain"]["identity_contract"]["stable_internal_id"] == "install_id"
    assert layers["personal_captain"]["identity_contract"]["display_name"] == "editable_metadata_only"
    assert layers["personal_captain"]["identity_contract"]["mode_switching"] == "not_supported"
    assert layers["specialist_agents"]["role"] == "scoped_operational_workers"
    assert layers["specialist_agents"]["modes"] == [
        "owner_edit",
        "owner_test",
        "customer_live",
    ]
    assert layers["specialist_agents"]["mode_defaults"]["default_mode"] == "owner_edit"
    assert layers["specialist_agents"]["mode_defaults"]["prompt_config_editing_requires"] == "owner_edit"
    assert layers["specialist_agents"]["runtime_boundary"] == "separate_scoped_sandbox"
    assert layers["applications"]["role"] == "product_modules"
    assert layers["applications"]["inherits_sage_memory"] is False
    assert layers["applications"]["inherits_specialist_memory"] is False
    assert captain_specialist["runtime_boundaries"]["platform_boundary"] == "control_plane_and_brokers"

    runtime = application_runtime["application_runtime"]
    assert runtime["scope_model"] == "app_scoped_separate_from_captain_and_specialists"
    assert application_runtime["direct_capabilities"]["denied_by_default"] == [
        "read_sage_memory",
        "read_specialist_memory",
        "access_user_private_context_without_explicit_contract",
        "call_unrestricted_tools_outside_app_policy",
        "silently_impersonate_sage",
        "silently_impersonate_specialist",
    ]
    assert application_runtime["bridge_contracts"]["app_to_sage"] == [
        "summary_request",
        "context_import_request",
        "recommendation_request",
        "delegation_request",
    ]
    assert application_runtime["context_envelope"]["inherits_sage_memory_by_default"] is False
    assert application_runtime["context_envelope"]["inherits_specialist_memory_by_default"] is False


def test_activity_local_cluster_and_hybrid_maps_preserve_safe_summary_boundaries() -> None:
    activity_timeline = _read_json(ACTIVITY_TIMELINE_MAP)
    local_runtime_cluster = _read_json(LOCAL_RUNTIME_CLUSTER_MAP)
    hybrid_sync_policy = _read_json(HYBRID_SYNC_POLICY_MAP)

    assert activity_timeline["surface_placement"]["notifications"] == "lightweight_daily_stream"
    assert activity_timeline["surface_placement"]["desktop_power_timeline"] == "deeper_reviewable_history"
    assert activity_timeline["surface_placement"]["mobile"] == "safe_summaries_only"
    assert activity_timeline["sage_ingestion"]["raw_internal_access"] is False

    assert local_runtime_cluster["cluster_model"]["required_parts"] == [
        "local_sage_runtime",
        "local_specialist_runtimes",
        "_archived_local_runtime_supervisor",
        "local_memory_stores",
        "local_artifact_bridge",
    ]
    assert local_runtime_cluster["local_memory"]["private_memory_default"] == "local_only"
    assert local_runtime_cluster["local_memory"]["automatic_full_sync_to_cloud"] is False

    assert hybrid_sync_policy["sync_classes"] == [
        "local_only",
        "sync_allowed",
        "summary_bridge_only",
        "explicit_opt_in",
    ]
    assert hybrid_sync_policy["fallback_behavior"]["local_offline"]["local_only_tasks"] == "wait_or_fail_closed"
    assert hybrid_sync_policy["fallback_behavior"]["hybrid_degraded_mode"]["fallback"] == "policy_safe_only"
    assert "unrestricted_raw_local_private_memory" in hybrid_sync_policy["summary_bridge"]["forbidden_payloads"]


def test_frontend_activity_route_uses_session_and_workspace_guards() -> None:
    text = _read(FRONTEND_ACTIVITY_ROUTE)

    assert "enforceBffRouteGuard" in text
    assert "requireControlPlaneSession" in text
    assert "requireControlPlaneWorkspaceAccess" in text
    assert "runtimeJsonRequest(`/activity/timeline?" in text

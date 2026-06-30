from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = ROOT / "docs"

CONTEXT_DOC = DOCS_DIR / "context.md"
BIBLE_DOC = DOCS_DIR / "bible.md"
PROJECT_MAP_DOC = DOCS_DIR / "project-map.md"
FRONTEND_MAP_DOC = DOCS_DIR / "frontend-map.md"
PENDING_TASKS_DOC = DOCS_DIR / "pending-tasks.md"

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

MOBILE_TABS_LAYOUT = ROOT / "mobile" / "app" / "(tabs)" / "_layout.tsx"
FRONTEND_ACTIVITY_ROUTE = ROOT / "frontend" / "app" / "api" / "activity" / "timeline" / "route.ts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(_read(path))


def test_docs_lockdown_exposes_only_the_canonical_handoff_files() -> None:
    assert {path.name for path in DOCS_DIR.iterdir() if path.is_file()}.issuperset({
        "bible.md",
        "context.md",
        "frontend-map.md",
        "gateway-architecture.md",
        "gateway-protocol.md",
        "pending-tasks.md",
        "personal-vs-studio-channel-model.md",
        "project-map.md",
    })


def test_context_records_the_four_layer_model_and_surface_truth() -> None:
    text = _read(CONTEXT_DOC)

    assert "Latest verified green commit: `b3eca81`" in text
    assert "## Surface Shell Model" in text
    assert "Shell classes are explicit:" in text
    assert "- `full_shell`" in text
    assert "- mobile" in text
    assert "- web" in text
    assert "- desktop" in text
    assert "- `channel_shell`" in text
    assert "- Telegram" in text
    assert "- WhatsApp" in text
    assert "- every shell shares the same captain identity" in text
    assert "- every shell shares the same run engine truth" in text
    assert "- channel shells are lightweight conversation surfaces, not separate product brains" in text
    assert "- channel shells must not become deep admin surfaces" in text
    assert "- channel shells must not become separate policy engines" in text
    assert "## The 4-Layer OS Model" in text
    assert "### 1. Sage / Personal Captain" in text
    assert "### 2. Specialist Workers" in text
    assert "### 3. Applications" in text
    assert "### 4. Platform Control Plane" in text
    assert "- a stable captain install identity with editable display metadata" in text
    assert "- stable internal id stays bound to the captain install" in text
    assert "- captain does not support owner_edit, owner_test, or customer_live mode switching" in text
    assert "- an explicit operating mode:" in text
    assert "- `owner_edit` is the authoring mode and allows prompt/config edits" in text
    assert "- `customer_live` is the external-audience mode and does not allow prompt/config edits" in text
    assert "### Shared Operational Board" in text
    assert "- shared instructions" in text
    assert "- SOP and playbook entries" in text
    assert "- `propose_update`" in text
    assert "- raw shared memory" in text
    assert "- published shared operational board entries" in text
    assert "The system must fail closed if a placement or sync rule cannot be satisfied." in text
    assert "Allowed state layers are frozen as:" in text
    assert "- captain private memory" in text
    assert "- specialist private memory" in text
    assert "- shared operational board" in text
    assert "- artifacts/history" in text
    assert "Artifact/history boundary:" in text
    assert "- cross-install exchange happens through explicit artifacts/history only" in text
    assert "- raw private-memory embeds are not an allowed artifact exchange path" in text
    assert "Sandbox and broker truth:" in text
    assert "- cross-install private memory is denied by default" in text
    assert "- specialist-to-captain private memory access is denied by default" in text
    assert "Mobile tabs are fixed:" in text
    assert "- Home" in text
    assert "- Chat" in text
    assert "- Applications" in text
    assert "- Notifications" in text
    assert "- Profile" in text
    assert "- `docs/` is the only canonical handoff" in text
    assert "## Auth And Public Ingress Boundary" in text
    assert "- `ORION_AUTH_REQUIRED=1` is the normal protected mode" in text
    assert "- `ORION_AUTH_REQUIRED=0` is explicit local-dev mode only" in text
    assert "- `ENV=production` rejects auth-disabled mode" in text
    assert "- `ORION_LOCAL_DEV_AUTH_ROLE`" in text
    assert "- `ORION_LOCAL_DEV_WORKSPACE_IDS`" in text
    assert "- `/channels/slack/events`" in text
    assert "- `/channels/github/webhook`" in text
    assert "- `/connectors/discord/webhook`" in text
    assert "- `/channels/whatsapp/twilio/webhook`" in text
    assert "- `/channels/telegram/autopilot/status`" in text
    assert "Connector classes are explicit:" in text
    assert "- `api_connector`" in text
    assert "- `browser_connector`" in text
    assert "- `media_generation_connector`" in text
    assert "- API-rich systems default to `api_connector`" in text
    assert "- browser-based control is a fallback path, not the default path, for API-rich systems" in text
    assert "- deep connector actions must execute through either:" in text
    assert "- Telegram, WhatsApp, Slack, Discord, email, phone, and web chat are shell surfaces" in text
    assert "- media-generation outputs default to the managed export bridge" in text
    assert "## Proven Runtime Truth Through Phase 53" in text
    assert "- `app_to_sage` and `app_to_specialist` typed bridge requests can execute through canonical install-backed turns" in text
    assert "- rendered web auth/session works through the current shell" in text
    assert "## Demo Reality Through Phase 53" in text
    assert "- cloud rendered: partial" in text
    assert "- local rendered: no" in text
    assert "- hybrid rendered: no" in text
    assert "- degraded safe mode: partial" in text


def test_bible_records_fail_closed_auth_and_public_webhook_rules() -> None:
    text = _read(BIBLE_DOC)

    assert "### 5. Fail Closed, Not Open" in text
    assert "If policy, placement, sync, entitlement, or approval state is unclear, the system must fail closed." in text
    assert "`ORION_AUTH_REQUIRED=0`: enter explicit local-dev identity mode only; never silently grant anonymous owner/admin power" in text
    assert "`ENV=production` with auth disabled: reject the request path rather than silently running without auth" in text
    assert "public webhooks without required provider verification headers or keys: reject or disable the path before parsing, dispatch, or run creation" in text
    assert "### 6. Strict API Gateways Only" in text
    assert "- no UI-side bypasses" in text
    assert "- no connector or secret access from the surface layer" in text
    assert "- protected API routes default to backend auth" in text
    assert "- intentional public ingress must be explicitly documented and limited to provider-verified webhook boundaries" in text


def test_project_map_tracks_active_platform_roots_and_ci_truth() -> None:
    text = _read(PROJECT_MAP_DOC)

    assert "Latest verified green commit: `b3eca81`" in text
    assert "/Users/mansur/Multi_Agent_Orchestrator_Project/server_modules" in text
    assert "/Users/mansur/Multi_Agent_Orchestrator_Project/frontend" in text
    assert "/Users/mansur/Multi_Agent_Orchestrator_Project/mobile" in text
    assert "/Users/mansur/Multi_Agent_Orchestrator_Project/src-tauri" in text
    assert "/Users/mansur/Multi_Agent_Orchestrator_Project/empyralis-supervisor" in text
    assert "Shell classes are frozen as:" in text
    assert "- `full_shell`" in text
    assert "- `channel_shell`" in text
    assert "Channel shells may do:" in text
    assert "- lightweight approvals where supported" in text
    assert "Channel shells may not become:" in text
    assert "- deep admin surfaces" in text
    assert "- separate product brains" in text
    assert "- `server_modules/auth.py`" in text
    assert "- `server_modules/connectors_actions.py`" in text
    assert "- `server_modules/runtime_runtime_api.py`" in text
    assert "- `server_modules/shared_operational_board_service.py`" in text
    assert "- explicit local queue pressure, retry, and dead-letter baseline for operator surfaces" in text
    assert "- captain identity metadata and stable-install projection" in text
    assert "- specialist mode contracts and authoring-mode gating" in text
    assert "- shared operational board storage, permissions, and version history" in text
    assert "- explicit state-layer model (`captain private`, `specialist private`, `shared operational board`, `artifacts/history`)" in text
    assert "- `ci.yml`" in text
    assert "- `security-baseline.yml`" in text
    assert "- `supply-chain.yml`" in text
    assert "- `build.yml`" in text
    assert "### Current Auth And Public Ingress Boundary" in text
    assert "- `server_modules/runtime_common.py`" in text
    assert "- `/channels/slack/events`" in text
    assert "- `/channels/github/webhook`" in text
    assert "- `/connectors/discord/webhook`" in text
    assert "- `/channels/whatsapp/twilio/webhook`" in text
    assert "operational status and autopilot profile routes remain protected by `require_api_key`" in text
    assert "server_modules/tests/test_golden_path.py" in text
    assert "serious first-send task requests now promote into the durable run path" in text
    assert "install-backed `app_to_sage` and `app_to_specialist` bridge execution" in text
    assert "captain display metadata projected against a stable captain install id" in text
    assert "- shared operational board projection into unified memory" in text
    assert "- `owner_edit`" in text
    assert "- `customer_live`" in text
    assert "- connector class contracts (`api_connector`, `browser_connector`, `media_generation_connector`)" in text
    assert "- sandbox/runtime state-layer policy propagation" in text
    assert "- brokered connector execution and approval gating" in text
    assert "- API-first connector routing with browser fallback only where needed" in text
    assert "- inbound channel routing and channel-shell separation" in text
    assert "- artifact export and managed file-bridge rules for connector outputs" in text
    assert "## Frozen Frontend And BFF Contract Boundary" in text
    assert "- `/api/control-plane/session`" in text
    assert "- `/api/turn`" in text
    assert "- `/api/runs`" in text
    assert "- `/api/approvals/resolve`" in text
    assert "The rebuild may not change:" in text
    assert "- `/turn` versus `/runs` contract meaning" in text


def test_frontend_map_keeps_dumb_ui_and_contract_shared_surfaces() -> None:
    text = _read(FRONTEND_MAP_DOC)

    assert "This is a strict dumb-UI strategy." in text
    assert "Mobile stays the daily-use surface with the current mounted Expo tab contract:" in text
    assert "- Chat" in text
    assert "- Build" in text
    assert "- Activity" in text
    assert "- Settings" in text
    assert "Internal `/apps/...` routes are hidden tool/runtime routes" in text
    assert "### Channel Shells" in text
    assert "- Telegram" in text
    assert "- WhatsApp" in text
    assert "They may do:" in text
    assert "- lightweight approvals where supported" in text
    assert "They may not become:" in text
    assert "- deep admin surfaces" in text
    assert "- separate product brains" in text
    assert "They still share the same captain identity and run engine truth as full shells." in text
    assert "Mobile and desktop-power must use the same backend semantics" in text
    assert "- `frontend/app/api/activity/timeline/route.ts`" in text
    assert "- `mobile/app/(tabs)/_layout.tsx`" in text
    assert "- Radix primitives for interaction, accessibility, layering, focus, and composition" in text
    assert "- Framer Motion for motion orchestration and transitions" in text
    assert "Backend and BFF contract alignment is proven for:" in text
    assert "- `/runs`" in text
    assert "- `/approvals`" in text
    assert "Current rendered truth:" in text
    assert "- the current web shell can render a real cloud-backed assistant answer" in text
    assert "- the current web shell now routes serious first-send task requests into the durable run path" in text
    assert "- lightweight question-and-answer chat is still allowed to stay on the direct chat path" in text


def test_gateway_architecture_doc_freezes_local_gateway_boundary() -> None:
    text = _read(DOCS_DIR / "gateway-architecture.md")

    assert "# Gateway Architecture" in text
    assert "`empyralis-gateway`" in text
    assert "`empyralis-supervisor`" in text
    assert "cloud control plane" in text.lower()
    assert "Legacy Socket.IO Bridge Is Removed" in text
    assert "canonical gateway plus supervisor boundary" in text
    assert "No Second Auth Plane" in text
    assert "local_companion" in text
    assert "Phase 0 does **not** do any of the following:" in text


def test_gateway_protocol_doc_freezes_phase_one_message_family() -> None:
    text = _read(DOCS_DIR / "gateway-protocol.md")

    assert "# Gateway Protocol" in text
    assert "The canonical transport is:" in text
    assert "gateway.connect" in text
    assert "gateway.hello" in text
    assert "gateway.heartbeat" in text
    assert "gateway.presence" in text
    assert "gateway.disconnect" in text
    assert "gateway.state.update" in text
    assert "The protocol must not create a second runtime-only auth plane." in text
    assert "tool.invoke" in text
    assert "channel.inbound" in text


def test_personal_vs_studio_channel_doc_freezes_channel_boundary() -> None:
    text = _read(DOCS_DIR / "personal-vs-studio-channel-model.md")

    assert "# Personal Vs Studio Channel Model" in text
    assert "Personal channels terminate at `empyralis-gateway`".lower() in text.lower()
    assert "Studio/business channels stay in the connector stack".lower() in text.lower()
    assert "Twilio WhatsApp is not personal WhatsApp".lower() in text.lower()
    assert "Telegram bot API vs Telegram personal MTProto".lower() in text.lower()
    assert "## Frozen Rebuild Boundary" in text
    assert "Personal channel sessions must not depend on the Studio webhook stack".lower() in text.lower()
    assert "The current `local_companion` concept is only a partial local-runtime substrate.".lower() in text.lower()


def test_pending_tasks_focus_on_execution_not_more_architecture_sprawl() -> None:
    text = _read(PENDING_TASKS_DOC)

    assert "Proven WORKING after phases 40-60:" in text
    assert "### 1. Durable Rendered Demo Completion" in text
    assert "### 2. Full Frontend Rebuild From Frozen Contracts" in text
    assert "### 3. End-To-End Dogfood Readiness" in text
    assert "### 4. Hybrid Summary Bridge Transport" in text
    assert "### 5. Shared Placement Enforcement Everywhere" in text
    assert "### 6. Activity UI Completion" in text
    assert "### 7. App-Agent Bridge Productization" in text
    assert "### 8. Local Cluster Operator Surface" in text
    assert "### 9. Specialist Admin Surface" in text
    assert "### 10. Scale And Capacity Beyond The Baseline" in text
    assert "- full backend suite passes locally in this workspace" in text
    assert "- minimum scale-safety baseline for durable intake, queueing, provider cooldown/backpressure, and retry/dead-letter visibility" in text
    assert "## Demo Gate Freeze" in text
    assert "- cloud rendered: PARTIAL" in text
    assert "- local rendered: NO" in text
    assert "- hybrid rendered: NO" in text
    assert "- degraded safe mode: PARTIAL" in text
    assert "Demo gate: NO." in text
    assert "Beta gate: NO." in text
    assert "Local `node` and `npm` are available in this audit environment" in text
    assert "Do not create new architecture sprawl." in text


def test_workflow_baseline_covers_backend_typecheck_and_supply_chain() -> None:
    ci = _read(CI_WORKFLOW)
    security = _read(SECURITY_WORKFLOW)
    supply_chain = _read(SUPPLY_CHAIN_WORKFLOW)
    release = _read(RELEASE_WORKFLOW)

    assert "Run demo-critical server test suite" in ci
    assert "python -m pytest \\" in ci
    assert "./node_modules/.bin/tsc --noEmit" in ci
    assert "cargo build --manifest-path empyralis-supervisor/Cargo.toml" in ci
    assert "actions/dependency-review-action@v4" in security
    assert "gitleaks/gitleaks-action@v2" in security
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


def test_product_surface_map_and_mobile_tabs_align_with_surface_truth() -> None:
    payload = _read_json(PRODUCT_SURFACE_MAP)
    mobile_tabs_layout = _read(MOBILE_TABS_LAYOUT)

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
    assert 'options={{ title: "Chat" }}' in mobile_tabs_layout
    assert 'options={{ title: "Build" }}' in mobile_tabs_layout
    assert 'options={{ title: "Discover" }}' not in mobile_tabs_layout
    assert '<Tabs.Screen name="apps/index" options={{ href: null }} />' in mobile_tabs_layout
    assert 'options={{ title: "Activity" }}' in mobile_tabs_layout
    assert 'options={{ title: "Settings" }}' in mobile_tabs_layout


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
        "local_runtime_supervisor",
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

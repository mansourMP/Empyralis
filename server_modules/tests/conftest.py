from __future__ import annotations

import asyncio
import inspect
import uuid
import warnings
from pathlib import Path

import pytest

asyncio.iscoroutinefunction = inspect.iscoroutinefunction


warnings.filterwarnings(
    "error",
    category=DeprecationWarning,
    module=r"server_modules(\..*)?",
)


# ---------------------------------------------------------------------------
# Real per-operation next_action tables, ported from the Rust kernel so the
# non-kernel test mock below can reproduce its "allow"-path behavior exactly
# instead of a blanket echo. Each table cites the kernel source it mirrors;
# re-derive it from there if the kernel's operation set changes.
# ---------------------------------------------------------------------------

# empyralis-runtime-kernel/src/runtime_state_store.rs next_action(), lines
# 1106-1216 (decision == "allow" arm only — "block"/"require_approval" return
# fixed sentinels handled separately below). Most operations are identity,
# but several are not (e.g. "upsert_workspace_memory" -> "write_workspace_memory",
# "upsert_sage_memory_entry" -> "write_sage_memory_entry") — that mismatch was
# the root cause of the memory_service.py / sage_memory_service.py failures.
_RUNTIME_STATE_STORE_NEXT_ACTIONS: dict[str, str] = {
    "init_schema": "initialize_state_schema",
    "upsert_live_run": "write_live_run_state",
    "delete_live_run": "delete_live_run_state",
    "archive_run": "write_run_archive",
    "create_or_update_approval_request": "write_run_approval_request",
    "resolve_approval_if_pending": "resolve_run_approval",
    "record_approval_resolution": "record_run_approval_resolution",
    "upsert_runtime_registration": "write_runtime_registration",
    "upsert_fleet_queue_partition": "write_fleet_queue_partition",
    "upsert_runtime_session": "write_runtime_session",
    "delete_runtime_session": "delete_runtime_session",
    "upsert_runtime_session_turn": "write_runtime_session_turn",
    "delete_runtime_session_turn": "delete_runtime_session_turn",
    "upsert_chat_stream_state": "write_chat_stream_state",
    "append_channel_event": "append_channel_event",
    "upsert_local_claim": "write_local_claim",
    "release_local_claim": "release_local_claim",
    "upsert_notification": "write_notification",
    "mark_notification_read": "mark_notification_read",
    "register_notification_device": "write_notification_device",
    "update_notification_delivery": "write_notification_delivery",
    "set_kill_switch": "set_kill_switch",
    "clear_kill_switch": "clear_kill_switch",
    "set_runtime_tool_enabled": "set_runtime_tool_enabled",
    "upsert_agent_computer_policy": "upsert_agent_computer_policy",
    "set_safe_mode_state": "set_safe_mode_state",
    "upsert_security_control_state": "upsert_security_control_state",
    "upsert_sage_profile": "upsert_sage_profile",
    "save_mcp_server_registry": "save_mcp_server_registry",
    "save_installed_skill_registry": "save_installed_skill_registry",
    "write_vault_key_file": "write_vault_key_file",
    "write_vault_blob_backup": "write_vault_blob_backup",
    "read_vault_blob_backup": "read_vault_blob_backup",
    "write_channel_pairings_backup": "write_channel_pairings_backup",
    "write_jwt_secret_file": "write_jwt_secret_file",
    "persist_artifact_record": "persist_artifact_record",
    "write_hosted_sandbox_base_image": "write_hosted_sandbox_base_image",
    "save_cli_companion_state": "save_cli_companion_state",
    "write_hosted_worker_output": "write_hosted_worker_output",
    "save_marketplace_distribution_state": "save_marketplace_distribution_state",
    "write_sage_dreaming_memory_state": "write_sage_dreaming_memory_state",
    "write_sage_dreaming_staging_file": "write_sage_dreaming_staging_file",
    "save_mini_apps_state": "save_mini_apps_state",
    "initialize_workspace_context_file": "initialize_workspace_context_file",
    "save_workspace_context_file": "save_workspace_context_file",
    "save_agent_computer_profile_state": "save_agent_computer_profile_state",
    "write_profile_api_file": "write_profile_api_file",
    "write_runtime_common_json": "write_runtime_common_json",
    "write_acp_manager_json": "write_acp_manager_json",
    "write_session_diagnostics_file": "write_session_diagnostics_file",
    "append_agent_memory_daily_log": "append_agent_memory_daily_log",
    "write_no_provider_summary": "write_no_provider_summary",
    "write_public_bot_drill_report": "write_public_bot_drill_report",
    "write_telegram_media_file": "write_telegram_media_file",
    "write_machine_capability_probe_file": "write_machine_capability_probe_file",
    "write_dropbox_download_file": "write_dropbox_download_file",
    "write_generated_image_file": "write_generated_image_file",
    "write_telegram_poll_lock_file": "write_telegram_poll_lock_file",
    "write_skills_registry_file": "write_skills_registry_file",
    "write_deployed_agent_knowledge_file": "write_deployed_agent_knowledge_file",
    "write_outcome_pack_spreadsheet_file": "write_outcome_pack_spreadsheet_file",
    "write_outcome_pack_document_file": "write_outcome_pack_document_file",
    "write_outcome_pack_remote_sync_file": "write_outcome_pack_remote_sync_file",
    "write_artifact_content_file": "write_artifact_content_file",
    "execute_external_write_once": "execute_external_write_once",
    "create_gateway_tool_approval": "create_gateway_tool_approval",
    "resolve_gateway_tool_approval": "resolve_gateway_tool_approval",
    "expire_gateway_tool_approval": "expire_gateway_tool_approval",
    "fail_gateway_tool_approval": "fail_gateway_tool_approval",
    "execute_gateway_tool_approval": "execute_gateway_tool_approval",
    "send_gateway_protocol_request_frame": "send_gateway_protocol_request_frame",
    "acquire_channel_execution_lease": "acquire_channel_execution_lease",
    "release_channel_execution_lease": "release_channel_execution_lease",
    "start_direct_tool_execution": "start_direct_tool_execution",
    "block_direct_tool_execution": "block_direct_tool_execution",
    "fail_direct_tool_execution": "fail_direct_tool_execution",
    "complete_direct_tool_execution": "complete_direct_tool_execution",
    "upsert_workspace_memory": "write_workspace_memory",
    "delete_workspace_memory": "delete_workspace_memory",
    "append_workspace_daily_log": "append_workspace_daily_log",
    "update_workspace_context_file": "write_workspace_context_file",
    "upsert_approval_memory_rule": "write_approval_memory_rule",
    "consume_approval_memory_rule": "consume_approval_memory_rule",
    "create_sage_approval": "create_sage_approval",
    "resolve_sage_approval": "resolve_sage_approval",
    "consume_sage_approval": "consume_sage_approval",
    "expire_sage_approvals": "expire_sage_approvals",
    "update_sage_service_profile": "update_sage_service_profile",
    "create_sage_service_entry": "create_sage_service_entry",
    "update_sage_service_entry": "update_sage_service_entry",
    "delete_sage_service_entry": "delete_sage_service_entry",
    "set_sage_service_entry_pinned": "set_sage_service_entry_pinned",
    "upsert_sage_memory_entry": "write_sage_memory_entry",
    "update_sage_memory_entry": "update_sage_memory_entry",
    "delete_sage_memory_entry": "delete_sage_memory_entry",
    "wipe_sage_memory": "wipe_sage_memory",
    "append_session_transcript": "append_session_transcript",
    "append_activity_ledger_event": "append_activity_ledger_event",
    "write_shared_operational_board_entry": "write_shared_operational_board_entry",
    "checkpoint_snapshot": "write_checkpoint_snapshot",
    "prune_records": "prune_state_records",
}
_RUNTIME_STATE_STORE_DEFAULT_NEXT_ACTION = "review_state_store_write"  # rust line 1215


def _mock_runtime_state_store_next_action(operation: str) -> str:
    op = str(operation or "").strip()
    return _RUNTIME_STATE_STORE_NEXT_ACTIONS.get(op, _RUNTIME_STATE_STORE_DEFAULT_NEXT_ACTION)


# empyralis-runtime-kernel/src/control_plane_service.rs, allow-path only
# (control_plane_service_decision_command(), lines 289-537). The set
# membership tables below are copied verbatim from the Rust consts; the
# next_action derivation mirrors lines 517-537 for the case this mock always
# produces — decision == "allow" (no block_reasons / approval_reasons).
_CONTROL_PLANE_SERVICE_DESTRUCTIVE_OPERATIONS = {  # rust lines 102-113
    "workspace_archive",
    "workspace_delete",
    "membership_remove",
    "invite_revoke",
    "thread_archive",
    "thread_delete",
    "workspace_emergency_stop",
    "external_user_privacy_delete",
    "deployed_agent_scope_data_delete",
    "workspace_scope_data_delete",
}
_CONTROL_PLANE_SERVICE_OWNER_REQUIRED_OPERATIONS = {  # rust lines 115-167
    "tenant_update",
    "transparency_settings_update",
    "workspace_policy_update",
    "workspace_delete",
    "billing_update",
    "workspace_billing_plan_update",
    "workspace_billing_account_write",
    "workspace_billing_subscription_write",
    "security_control_state_write",
    "governance_hold_write",
    "governance_hold_release",
    "channel_user_acquisition_touch_write",
    "channel_user_acquisition_conversion_write",
    "deployed_agent_upgrade_click_write",
    "agent_channel_event_write",
    "personal_context_event_write",
    "personal_context_event_seen_update",
    "agent_action_event_write",
    "agent_secret_access_event_write",
    "agent_egress_event_write",
    "activity_ledger_event_write",
    "agent_trace_create",
    "agent_trace_event_write",
    "agent_trace_finish",
    "agent_turn_transcript_event_append",
    "agent_thread_ensure",
    "agent_session_upsert",
    "agent_session_terminate",
    "agent_turn_upsert",
    "knowledge_source_upsert",
    "knowledge_source_chunks_replace",
    "knowledge_retrieval_event_write",
    "compiled_workflow_artifact_create",
    "workspace_agent_install_compiled_artifact_update",
    "self_hosted_enrollment_intent_create",
    "self_hosted_runtime_enroll",
    "self_hosted_runtime_approve",
    "self_hosted_runtime_heartbeat",
    "self_hosted_command_enqueue",
    "self_hosted_command_claim",
    "self_hosted_command_complete",
    "quota_update",
    "admin_impersonation",
    "workspace_emergency_stop",
    "public_route_update",
    "workspace_scope_data_delete",
    "deployed_agent_daily_message_quota_consume",
    "deployed_agent_daily_message_warning_update",
    "deployed_agent_cost_ledger_write",
    "workspace_hosted_ai_cost_ledger_write",
    "credit_ledger_event_write",
}
_CONTROL_PLANE_SERVICE_ADMIN_REQUIRED_OPERATIONS = {  # rust lines 169-221
    "workspace_create",
    "workspace_update",
    "workspace_archive",
    "membership_add",
    "membership_update",
    "membership_remove",
    "invite_create",
    "invite_revoke",
    "pilot_invite_create",
    "pilot_invite_revoke",
    "workspace_tenant_binding_ensure",
    "compiled_workflow_artifact_create",
    "workspace_agent_install_compiled_artifact_update",
    "self_hosted_enrollment_intent_create",
    "self_hosted_runtime_approve",
    "self_hosted_command_enqueue",
    "deployed_agent_record_write",
    "gateway_record_write",
    "session_record_write",
    "audit_export",
    "secret_reference_write",
    "external_user_privacy_delete",
    "deployed_agent_scope_data_delete",
    "workspace_scope_data_delete",
    "workspace_billing_account_write",
    "workspace_billing_subscription_write",
    "security_control_state_write",
    "governance_hold_write",
    "governance_hold_release",
    "channel_user_acquisition_touch_write",
    "channel_user_acquisition_conversion_write",
    "deployed_agent_upgrade_click_write",
    "agent_channel_event_write",
    "personal_context_event_write",
    "personal_context_event_seen_update",
    "agent_action_event_write",
    "agent_secret_access_event_write",
    "agent_egress_event_write",
    "activity_ledger_event_write",
    "agent_trace_create",
    "agent_trace_event_write",
    "agent_trace_finish",
    "agent_turn_transcript_event_append",
    "agent_thread_ensure",
    "agent_session_upsert",
    "agent_session_terminate",
    "agent_turn_upsert",
    "knowledge_source_upsert",
    "knowledge_source_chunks_replace",
    "knowledge_retrieval_event_write",
    "workspace_tenant_binding_ensure",
}
_CONTROL_PLANE_SERVICE_EXTERNAL_WRITE_OPERATIONS = {  # rust lines 223-274
    "webhook_ingest",
    "deployed_agent_record_write",
    "gateway_record_write",
    "session_record_write",
    "public_route_update",
    "workspace_ai_route_update",
    "secret_reference_write",
    "external_user_privacy_request_write",
    "external_user_privacy_audit_write",
    "external_user_privacy_delete",
    "deployed_agent_scope_data_delete",
    "workspace_scope_data_delete",
    "workspace_billing_defaults_write",
    "workspace_billing_plan_update",
    "workspace_billing_account_write",
    "workspace_billing_subscription_write",
    "security_control_state_write",
    "governance_hold_write",
    "governance_hold_release",
    "channel_user_acquisition_touch_write",
    "channel_user_acquisition_conversion_write",
    "deployed_agent_upgrade_click_write",
    "agent_channel_event_write",
    "personal_context_event_seen_update",
    "agent_action_event_write",
    "agent_secret_access_event_write",
    "agent_egress_event_write",
    "activity_ledger_event_write",
    "agent_trace_create",
    "agent_trace_event_write",
    "agent_trace_finish",
    "agent_turn_transcript_event_append",
    "agent_thread_ensure",
    "agent_session_upsert",
    "agent_session_terminate",
    "agent_turn_upsert",
    "knowledge_source_upsert",
    "knowledge_source_chunks_replace",
    "knowledge_retrieval_event_write",
    "workspace_tenant_binding_ensure",
    "user_profile_update",
    "compiled_workflow_artifact_create",
    "workspace_agent_install_compiled_artifact_update",
    "self_hosted_enrollment_intent_create",
    "self_hosted_runtime_enroll",
    "self_hosted_runtime_approve",
    "self_hosted_runtime_heartbeat",
    "self_hosted_command_enqueue",
    "self_hosted_command_claim",
    "self_hosted_command_complete",
}


def _mock_control_plane_service_next_action(operation: str, payload: dict) -> str:
    """Mirrors control_plane_service_decision_command()'s allow-path next_action
    (rust lines 517-537) — this mock never produces block/require_approval, so
    those branches (lines 517-520) are intentionally omitted."""
    op = str(operation or "").strip()
    target_status = str(payload.get("target_status") or "active").strip()
    source = str(payload.get("source") or "internal").strip()
    existing_record_idempotent_return = op == "invite_revoke" and target_status == "revoked"  # line 489-490
    if payload.get("dry_run"):
        return "return_control_plane_dry_run"
    if existing_record_idempotent_return:
        return "return_existing_control_plane_record"
    if op == "webhook_ingest":
        return "persist_webhook_event"
    if op == "entitlement_check":
        return "return_entitlement_snapshot"
    if op == "audit_export":
        return "create_audit_export_job"
    if op in _CONTROL_PLANE_SERVICE_DESTRUCTIVE_OPERATIONS:
        return "apply_control_plane_destructive_write"
    external_write = op in _CONTROL_PLANE_SERVICE_EXTERNAL_WRITE_OPERATIONS or source == "webhook"
    if (
        external_write
        or op in _CONTROL_PLANE_SERVICE_ADMIN_REQUIRED_OPERATIONS
        or op in _CONTROL_PLANE_SERVICE_OWNER_REQUIRED_OPERATIONS
    ):
        return "apply_control_plane_write"
    return "allow_control_plane_read"


# empyralis-runtime-kernel/src/control_plane.rs control_plane_decision_command(),
# lines 22-213. Identity-ish but with a "_record" suffix (and a
# read/status_transition special case) — not a blanket echo either.
_CONTROL_PLANE_RECORD_NEXT_ACTIONS = {  # rust lines 132-141
    "create": "create_record",
    "upsert": "upsert_record",
    "update": "update_record",
    "archive": "archive_record",
    "restore": "restore_record",
    "lock": "lock_record",
    "unlock": "unlock_record",
}


def _mock_control_plane_record_next_action(operation: str, payload: dict) -> str:
    op = str(operation or "").strip()
    if op in ("read", "list", "lookup"):  # rust lines 72-85
        return "read_record"
    if op == "status_transition":  # rust lines 158-213
        current_status = str(payload.get("current_status") or "").strip()
        next_status = str(payload.get("next_status") or "").strip()
        if current_status and next_status and current_status == next_status:
            return "noop"
        return "write_status_transition"
    return _CONTROL_PLANE_RECORD_NEXT_ACTIONS.get(op, "")


@pytest.fixture(autouse=True)
def _skip_kernel_tests_when_binary_missing(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Skip @pytest.mark.kernel tests when the Rust kernel binary is absent.

    For non-kernel tests, monkeypatch run_runtime_kernel to return a mock
    "allow" decision so business-logic tests are not blocked by a missing
    compiled binary.  Production kernel gates are NOT modified — only the
    test-time call to the kernel is replaced.
    """
    from server_modules.rust_runtime_kernel_client import runtime_kernel_binary

    if request.node.get_closest_marker("kernel") is not None:
        if runtime_kernel_binary() is None:
            pytest.skip(
                "requires empyralis-runtime-kernel binary (not built in this environment)"
            )
        return  # kernel binary present — let the test use the real kernel

    # Non-kernel tests: mock out run_runtime_kernel so the missing binary
    # does not block business-logic test paths.
    def _mock_run_runtime_kernel(command: str, payload, timeout_seconds: int = 5):
        import copy
        normalized_payload = payload if isinstance(payload, dict) else {}

        # Several Rust kernel commands derive `next_action` from the
        # requested `operation` on an "allow" decision, but the mapping is
        # NOT always the identity function — callers (memory_service.py,
        # sage_memory_service.py, control_plane_repository.py, etc.)
        # compare next_action against the real kernel's per-operation
        # contract before they'll persist state, so a blanket echo produces
        # false gate failures for every non-identity operation. Each branch
        # below reproduces the real kernel source it's named after; see the
        # per-command helper functions above for file:line citations.
        next_action = ""
        if command == "runtime-state-store-decision":
            next_action = _mock_runtime_state_store_next_action(normalized_payload.get("operation"))
        elif command == "control-plane-service-decision":
            next_action = _mock_control_plane_service_next_action(
                normalized_payload.get("operation"), normalized_payload
            )
        elif command == "control-plane-decision":
            next_action = _mock_control_plane_record_next_action(
                normalized_payload.get("operation"), normalized_payload
            )

        return {
            "ok": True,
            "decision": "allow",
            "command": command,
            "decision_id": "rkd_mock_non_kernel_test",
            "reason": "mock allow (non-kernel test fixture)",
            "next_action": next_action,
            "payload": copy.deepcopy(normalized_payload),
        }

    try:
        from server_modules import rust_runtime_kernel_client as _rk
    except Exception:
        return
    monkeypatch.setattr(_rk, "run_runtime_kernel", _mock_run_runtime_kernel)


@pytest.fixture(autouse=True)
def _isolate_empyralis_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Keep tests from reading or writing the developer's real local state."""
    state_home = tmp_path / "empyralis-state"
    auth_db = state_home / "auth" / "users.db"
    gateway_db = state_home / "gateway" / "gateway-state.sqlite3"
    personal_channels_db = state_home / "channels" / "personal-channels.sqlite3"
    runtime_db = state_home / "runtime" / "state.db"
    setup_sessions_file = state_home / "setup" / "sessions.json"
    provider_profiles_file = state_home / "providers" / "profiles.json"
    idempotency_file = state_home / "runtime" / "idempotency.json"
    for path in (auth_db, gateway_db, personal_channels_db, runtime_db, setup_sessions_file, provider_profiles_file, idempotency_file):
        path.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("EMPYRALIS_STATE_HOME", str(state_home))
    monkeypatch.setenv("ORION_RUNTIME_STATE_DB", str(runtime_db))
    monkeypatch.setenv("EMPYRALIS_GATEWAY_STATE_DB", str(gateway_db))
    monkeypatch.setenv("ORION_SETUP_SESSIONS_FILE", str(setup_sessions_file))
    monkeypatch.setenv("ORION_PROVIDER_PROFILES_FILE", str(provider_profiles_file))
    monkeypatch.setenv("ORION_IDEMPOTENCY_FILE", str(idempotency_file))

    try:
        from server_modules import auth

        monkeypatch.setattr(auth, "AUTH_DB_FILE", auth_db, raising=False)
    except Exception:
        pass
    try:
        from server_modules import control_plane_repository

        monkeypatch.setattr(control_plane_repository, "LOCAL_IDENTITY_DB_FILE", auth_db, raising=False)
    except Exception:
        pass
    try:
        from server_modules import gateway_state_repository

        monkeypatch.setattr(gateway_state_repository, "GATEWAY_STATE_DB_FILE", gateway_db, raising=False)
    except Exception:
        pass
    try:
        from server_modules import personal_channels_repository

        monkeypatch.setattr(personal_channels_repository, "PERSONAL_CHANNELS_DB_FILE", personal_channels_db, raising=False)
    except Exception:
        pass
    try:
        from server_modules import runtime_config

        monkeypatch.setattr(runtime_config, "ORION_RUNTIME_STATE_DB", runtime_db, raising=False)
        monkeypatch.setattr(runtime_config, "ORION_SETUP_SESSIONS_FILE", setup_sessions_file, raising=False)
        monkeypatch.setattr(runtime_config, "ORION_PROVIDER_PROFILES_FILE", provider_profiles_file, raising=False)
        monkeypatch.setattr(runtime_config, "ORION_IDEMPOTENCY_FILE", idempotency_file, raising=False)
    except Exception:
        pass
    try:
        from server_modules import shared

        shared.sync_acp_manager_paths(
            runtime_db_path=runtime_db,
            setup_sessions_path=setup_sessions_file,
            provider_profiles_path=provider_profiles_file,
            idempotency_path=idempotency_file,
        )
    except Exception:
        pass


class _InMemoryDcrVault:
    """Stand-in for `vault_store` used by oauth_dynamic_client_store in tests.

    Rows live in a plain dict instead of Postgres, but the CIPHERTEXT is
    produced by the real vault encryption routine under a fixed test
    passphrase — so a test can still assert that a client_secret is stored
    encrypted rather than in the clear, without a database and without
    touching the developer's real vault.
    """

    PASSPHRASE = "test-only-dcr-vault-passphrase"

    def __init__(self) -> None:
        self.rows: dict = {}

    # -- encryption (real algorithm, test passphrase) --
    def _openssl_encrypt(self, plaintext: str) -> str:
        from server_modules import vault_store

        return vault_store._openssl_encrypt_with_passphrase(plaintext, self.PASSPHRASE)

    def _openssl_decrypt(self, ciphertext: str) -> str:
        from server_modules import vault_store

        return vault_store._openssl_decrypt_with_passphrase(ciphertext, self.PASSPHRASE)

    # -- single-row storage API --
    def get_credential(self, credential_id: str):
        return self.rows.get(str(credential_id))

    def list_credentials(self) -> list:
        return list(self.rows.values())

    def upsert_credential(self, entry: dict) -> dict:
        import copy

        stored = copy.deepcopy(entry)
        stored.setdefault("created_at", "2026-01-01T00:00:00Z")
        stored["updated_at"] = f"2026-01-01T00:00:{len(self.rows):02d}Z"
        self.rows[str(entry.get("id"))] = stored
        return stored

    def delete_credential(self, credential_id: str) -> bool:
        return self.rows.pop(str(credential_id), None) is not None


@pytest.fixture(autouse=True)
def dcr_client_vault(monkeypatch: pytest.MonkeyPatch) -> _InMemoryDcrVault:
    """Autouse guard: dynamically-registered OAuth clients (MAN-124) are now
    persisted in the encrypted credential vault. Without this, any test that
    exercises the DCR path would write real rows into whatever database
    DATABASE_URL points at — and read them back in the NEXT test session,
    which is exactly how these tests started failing for each other.

    Tests that want to inspect what was persisted can request this fixture.
    """
    fake = _InMemoryDcrVault()
    try:
        from server_modules import oauth_dynamic_client_store

        monkeypatch.setattr(oauth_dynamic_client_store, "_vault", lambda: fake)
    except Exception:
        pass
    return fake


@pytest.fixture
def second_real_user_in_workspace():
    """Factory fixture for multiplayer-correctness tests: register a REAL,
    distinct second user and (by default) attach them to a target
    workspace_id, all through the real repository/DB path -- never the
    synthetic current_user dict pattern used elsewhere (e.g.
    test_routes_fleet_delete_agent.py's _viewer_user()/_member_user(), which
    hand-builds a workspace_access map and never touches a database).

    Concretely, per call:
      1. auth.register_user(email, password) -- the exact function
         POST /auth/register calls -- persists a brand-new user row (and
         its own bootstrap workspace) into the SAME per-test SQLite file
         _isolate_empyralis_state above already points both auth.AUTH_DB_FILE
         and control_plane_repository.LOCAL_IDENTITY_DB_FILE at.
      2. auth.upsert_workspace_membership(user_id, workspace_id, role) --
         which wraps control_plane_repository.ensure_workspace_membership,
         the real repository write -- attaches that real user to the target
         workspace_id as a second, distinct member.
      3. auth._effective_workspace_access(...) reads the resulting
         membership rows back out of the real DB (via
         auth._list_workspace_memberships ->
         control_plane_repository.list_workspace_memberships_for_user) the
         same way auth.get_current_user builds workspace_access for a
         genuine bearer session -- so the returned current_user dict is
         DB-derived, not hand-typed.

    Returns {"user_id", "email", "current_user"}; hand `current_user` to
    app.dependency_overrides[get_current_user] = lambda: result["current_user"].

    Pass join_workspace=False to get a real, authenticated user who is NOT
    yet a member of workspace_id -- e.g. to drive the invite-accept endpoint
    itself and assert IT is what creates the membership.
    """

    def _make(
        workspace_id: str,
        *,
        role: str = "member",
        email: str | None = None,
        name: str | None = None,
        password: str = "Sup3r-Secret-Passw0rd!",
        join_workspace: bool = True,
    ) -> dict:
        from server_modules import auth

        clean_email = (email or f"invitee-{uuid.uuid4().hex[:10]}@example.com").strip().lower()
        auth.register_user(clean_email, password, name=name)
        user = auth._find_user_by_email(clean_email)
        assert isinstance(user, dict) and user.get("id"), (
            "real registration did not persist a user row for " + clean_email
        )
        user_id = str(user["id"]).strip()

        if join_workspace:
            auth.upsert_workspace_membership(user_id, workspace_id, role)

        workspace_access = auth._effective_workspace_access(
            user_id=user_id,
            email=clean_email,
            role=role,
            auth_type="bearer",
            is_admin=False,
            workspace_ids=[workspace_id] if join_workspace else [],
        )
        current_user = {
            "user_id": user_id,
            "auth_type": "bearer",
            "email": clean_email,
            "workspace_ids": list(workspace_access.keys()),
            "workspace_roles": {
                wid: entry.get("role") for wid, entry in workspace_access.items() if isinstance(entry, dict)
            },
            "workspace_access": workspace_access,
            "role": (workspace_access.get(workspace_id) or {}).get("role", "viewer"),
            "is_admin": False,
            "auth_admin": False,
        }
        return {"user_id": user_id, "email": clean_email, "current_user": current_user}

    return _make

use serde_json::{json, Value};

const OPERATIONS: &[&str] = &[
    "init_schema",
    "upsert_live_run",
    "delete_live_run",
    "archive_run",
    "create_or_update_approval_request",
    "resolve_approval_if_pending",
    "record_approval_resolution",
    "upsert_runtime_registration",
    "upsert_fleet_queue_partition",
    "upsert_runtime_session",
    "delete_runtime_session",
    "upsert_runtime_session_turn",
    "delete_runtime_session_turn",
    "upsert_chat_stream_state",
    "append_channel_event",
    "upsert_local_claim",
    "release_local_claim",
    "upsert_notification",
    "mark_notification_read",
    "register_notification_device",
    "update_notification_delivery",
    "set_kill_switch",
    "clear_kill_switch",
    "set_runtime_tool_enabled",
    "upsert_agent_computer_policy",
    "set_safe_mode_state",
    "upsert_security_control_state",
    "upsert_sage_profile",
    "save_mcp_server_registry",
    "save_installed_skill_registry",
    "write_vault_key_file",
    "write_vault_blob_backup",
    "read_vault_blob_backup",
    "write_channel_pairings_backup",
    "write_jwt_secret_file",
    "persist_artifact_record",
    "write_hosted_sandbox_base_image",
    "save_cli_companion_state",
    "write_hosted_worker_output",
    "save_marketplace_distribution_state",
    "write_sage_dreaming_memory_state",
    "write_sage_dreaming_staging_file",
    "save_mini_apps_state",
    "initialize_workspace_context_file",
    "save_workspace_context_file",
    "save_agent_computer_profile_state",
    "write_profile_api_file",
    "write_runtime_common_json",
    "write_acp_manager_json",
    "write_session_diagnostics_file",
    "append_agent_memory_daily_log",
    "write_no_provider_summary",
    "write_public_bot_drill_report",
    "write_telegram_media_file",
    "write_machine_capability_probe_file",
    "write_dropbox_download_file",
    "write_generated_image_file",
    "write_telegram_poll_lock_file",
    "write_skills_registry_file",
    "write_deployed_agent_knowledge_file",
    "write_outcome_pack_spreadsheet_file",
    "write_outcome_pack_document_file",
    "write_outcome_pack_remote_sync_file",
    "write_artifact_content_file",
    "execute_external_write_once",
    "create_gateway_tool_approval",
    "resolve_gateway_tool_approval",
    "expire_gateway_tool_approval",
    "fail_gateway_tool_approval",
    "execute_gateway_tool_approval",
    "send_gateway_protocol_request_frame",
    "acquire_channel_execution_lease",
    "release_channel_execution_lease",
    "start_direct_tool_execution",
    "block_direct_tool_execution",
    "fail_direct_tool_execution",
    "complete_direct_tool_execution",
    "upsert_workspace_memory",
    "delete_workspace_memory",
    "append_workspace_daily_log",
    "update_workspace_context_file",
    "upsert_approval_memory_rule",
    "consume_approval_memory_rule",
    "create_sage_approval",
    "resolve_sage_approval",
    "consume_sage_approval",
    "expire_sage_approvals",
    "update_sage_service_profile",
    "create_sage_service_entry",
    "update_sage_service_entry",
    "delete_sage_service_entry",
    "set_sage_service_entry_pinned",
    "upsert_sage_memory_entry",
    "update_sage_memory_entry",
    "delete_sage_memory_entry",
    "wipe_sage_memory",
    "append_session_transcript",
    "append_activity_ledger_event",
    "write_shared_operational_board_entry",
    "checkpoint_snapshot",
    "prune_records",
];

const SQLITE_CHECKPOINT_ONLY_CLASSES: &[&str] = &[
    "local_pending_queue",
    "local_claims",
    "live_runs",
    "runtime_registrations",
    "runtime_sessions",
    "runtime_session_turns",
    "live_runs",
    "channel_events",
    "chat_stream_state",
    "notifications",
    "notification_devices",
    "notification_delivery_statuses",
    "local_app_state",
];

const TERMINAL_RUN_STATUSES: &[&str] = &[
    "succeeded",
    // MAN-108 Bug 2: the Python run lifecycle's actual terminal vocabulary
    // (server_modules/shared.py's TERMINAL_RUN_STATUSES) is "completed" /
    // "failed" / "timeout" / "stopped" / "cancelled" -- it never uses
    // "succeeded" at all. Every durable run that actually finished its work
    // was archived with status="completed", which this list didn't
    // recognize as terminal, so archive_run always fell into the
    // archive_non_terminal_run_requires_review approval branch below --
    // 100% of the time, for every durable run that ever completed
    // successfully, not just task-assignment ones. Adding the real Python
    // terminal statuses here (not renaming/removing "succeeded", in case
    // another caller already relies on it) is a pure widening of what
    // counts as terminal, so it can only turn a previously-blocked/
    // approval-required archive into an allowed one -- never the reverse.
    "completed",
    "timeout",
    "stopped",
    "failed",
    "cancelled",
    "canceled",
    "terminated",
    "expired",
    "archived",
    // 2026-08-13: the MAN-108 widening above transcribed Python's list BY
    // HAND and dropped this one. server_modules/shared.py's
    // TERMINAL_RUN_STATUSES is {completed, failed, timeout,
    // waiting_for_input, stopped, cancelled} -- and a run really does reach
    // it (worker_dispatch_service.py:703 sets exactly this status on a run
    // that pauses for input). So archiving such a run still fell into
    // archive_non_terminal_run_requires_review: MAN-306's symptom, surviving
    // in the one status the fix for MAN-306 forgot to copy. A hand-copied
    // list goes stale the moment the source changes and says nothing when it
    // does -- see the drift test in
    // server_modules/tests/test_kernel_terminal_status_drift.py, which reads
    // BOTH lists from their own files so the expected set and the actual set
    // can never come from the same place again.
    "waiting_for_input",
];

const SESSION_STATUSES: &[&str] = &[
    "active",
    "running",
    "paused",
    "closed",
    "expired",
    "failed",
    "terminated",
];

const TURN_STATUSES: &[&str] = &[
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
    "canceled",
    "paused",
];

pub fn runtime_state_store_decision_command(payload: &Value) -> Value {
    let operation = normalize_operation(&text(payload, &["operation", "action"]));
    let state_class = normalize_or(
        &text(payload, &["state_class", "table", "record_type"]),
        default_state_class(&operation),
    );
    let storage_engine = normalize_or(
        &text(payload, &["storage_engine", "backend"]),
        "durable_postgres",
    );
    let workspace_id = text(payload, &["workspace_id"]);
    let tenant_id = normalize_or(&text(payload, &["tenant_id"]), "default");
    let run_id = text(payload, &["run_id"]);
    let session_id = text(payload, &["session_id", "runtime_session_id"]);
    let turn_id = text(payload, &["turn_id"]);
    let runtime_id = text(payload, &["runtime_id", "worker_id", "machine_id"]);
    let notification_id = text(payload, &["notification_id"]);
    let device_id = text(payload, &["device_id"]);
    let actor_id = text(payload, &["actor_id", "user_id", "owner_user_id"]);
    let trace_id = text(payload, &["trace_id"]);
    let status = normalize_or(&text(payload, &["status", "state"]), "unknown");
    let previous_status = normalize_or(
        &text(payload, &["previous_status", "from_state"]),
        "unknown",
    );
    let expected_version = opt_i64(payload, &["expected_version", "version"]);
    let current_version = opt_i64(payload, &["current_version"]);
    let payload_present = present(payload, &["payload", "record", "item", "snapshot"]);
    let payload_bytes = number(payload, &["payload_bytes", "record_bytes"], 0);
    let max_payload_bytes = number(payload, &["max_payload_bytes"], 1_048_576);
    let retention_days = number(payload, &["retention_days"], 30);
    let records_requested = number(payload, &["records_requested", "limit"], 100);
    let max_records = number(payload, &["max_records"], 1_000);
    let durable_runtime_required = flag(payload, &["durable_runtime_required"], true);
    let durable_pool_available = flag(payload, &["durable_pool_available", "pool_available"], true);
    let local_checkpoint_allowed = flag(payload, &["local_checkpoint_allowed"], true);
    let workspace_access = flag(
        payload,
        &["workspace_access", "workspace_access_allowed"],
        true,
    );
    let owner_access = flag(payload, &["owner_access", "owner_access_allowed"], true);
    let destructive_approval = flag(
        payload,
        &["destructive_approval", "approval_provided"],
        false,
    );
    let retention_lock = flag(payload, &["retention_lock"], false);
    let force = flag(payload, &["force"], false);

    let mut blocks: Vec<String> = Vec::new();
    let mut approvals: Vec<String> = Vec::new();
    let mut flags: Vec<&str> = Vec::new();

    if operation.is_empty() {
        blocks.push("runtime_state_operation_missing".to_string());
    } else if !OPERATIONS.contains(&operation.as_str()) {
        blocks.push(format!("unknown_runtime_state_operation:{operation}"));
    }
    if state_class.is_empty() {
        blocks.push("runtime_state_class_missing".to_string());
    }
    if storage_engine == "sqlite" || storage_engine == "local_sqlite" {
        flags.push("local_checkpoint_store");
        if !local_checkpoint_allowed {
            blocks.push("local_checkpoint_store_disabled".to_string());
        }
        if !SQLITE_CHECKPOINT_ONLY_CLASSES.contains(&state_class.as_str()) {
            blocks.push("sqlite_write_not_checkpoint_only".to_string());
        }
    } else {
        flags.push("durable_store");
        if durable_runtime_required && !durable_pool_available {
            blocks.push("durable_runtime_pool_unavailable".to_string());
        }
    }
    if requires_workspace(&operation) && workspace_id.is_empty() {
        blocks.push("workspace_id_missing".to_string());
    }
    if requires_workspace(&operation) && !workspace_access {
        blocks.push("workspace_access_denied".to_string());
    }
    if requires_owner_access(&operation) && !owner_access {
        blocks.push("owner_access_denied".to_string());
    }
    if requires_payload(&operation) && !payload_present {
        blocks.push("state_payload_missing".to_string());
    }
    if payload_bytes > max_payload_bytes {
        approvals.push("state_payload_exceeds_default_limit".to_string());
    }
    if payload_bytes > 16 * 1024 * 1024 {
        blocks.push("state_payload_exceeds_hard_limit".to_string());
    }
    if requires_run_id(&operation, &state_class) && run_id.is_empty() {
        blocks.push("run_id_missing".to_string());
    }
    if requires_session_id(&operation, &state_class) && session_id.is_empty() {
        blocks.push("session_id_missing".to_string());
    }
    if requires_turn_id(&operation, &state_class) && turn_id.is_empty() {
        blocks.push("turn_id_missing".to_string());
    }
    if requires_runtime_id(&operation, &state_class) && runtime_id.is_empty() {
        blocks.push("runtime_id_missing".to_string());
    }
    if operation == "upsert_live_run" {
        if TERMINAL_RUN_STATUSES.contains(&previous_status.as_str())
            && previous_status != status
            && !force
        {
            blocks.push("terminal_live_run_transition_blocked".to_string());
        }
        if version_conflict(expected_version, current_version) {
            blocks.push("run_state_version_conflict".to_string());
        }
    }
    if operation == "archive_run" {
        if !TERMINAL_RUN_STATUSES.contains(&status.as_str()) {
            approvals.push("archive_non_terminal_run_requires_review".to_string());
        }
        flags.push("archive_write");
    }
    if operation == "upsert_runtime_session" && !SESSION_STATUSES.contains(&status.as_str()) {
        blocks.push("invalid_runtime_session_status".to_string());
    }
    if operation == "upsert_runtime_session_turn" && !TURN_STATUSES.contains(&status.as_str()) {
        blocks.push("invalid_runtime_session_turn_status".to_string());
    }
    if matches!(
        operation.as_str(),
        "delete_runtime_session"
            | "delete_runtime_session_turn"
            | "delete_live_run"
            | "delete_workspace_memory"
            | "delete_sage_memory_entry"
            | "wipe_sage_memory"
            | "prune_records"
    ) {
        flags.push("destructive_state_operation");
        if retention_lock {
            blocks.push("retention_lock_blocks_delete".to_string());
        }
        if !destructive_approval && !safe_delete_status(&status) {
            approvals.push("destructive_state_operation_requires_approval".to_string());
        }
    }
    if operation == "append_channel_event" {
        flags.push("append_only_event");
        if trace_id.is_empty() {
            approvals.push("channel_event_without_trace_requires_review".to_string());
        }
    }
    if operation == "upsert_local_claim" {
        flags.push("claim_write");
        if runtime_id.is_empty() {
            blocks.push("claim_worker_id_missing".to_string());
        }
    }
    if operation == "release_local_claim" && runtime_id.is_empty() && !force {
        blocks.push("claim_release_worker_id_missing".to_string());
    }
    if operation == "upsert_notification" && notification_id.is_empty() {
        blocks.push("notification_id_missing".to_string());
    }
    if operation == "register_notification_device" && device_id.is_empty() {
        blocks.push("device_id_missing".to_string());
    }
    if operation == "mark_notification_read" && actor_id.is_empty() {
        blocks.push("notification_reader_missing".to_string());
    }
    if operation == "checkpoint_snapshot" {
        flags.push("checkpoint_snapshot");
        if storage_engine != "sqlite" && storage_engine != "local_sqlite" {
            approvals.push("checkpoint_snapshot_to_durable_store_requires_review".to_string());
        }
    }
    if operation == "prune_records" {
        if retention_days < 1 {
            blocks.push("retention_days_must_be_positive".to_string());
        } else if retention_days < 7 && !destructive_approval {
            approvals.push("short_retention_prune_requires_approval".to_string());
        }
        if records_requested > max_records {
            approvals.push("large_prune_batch_requires_approval".to_string());
        }
    }

    blocks.sort();
    blocks.dedup();
    approvals.sort();
    approvals.dedup();
    flags.sort();
    flags.dedup();

    let decision = if !blocks.is_empty() {
        "block"
    } else if !approvals.is_empty() && !destructive_approval {
        "require_approval"
    } else {
        "allow"
    };
    let reason = if !blocks.is_empty() {
        blocks.join("; ")
    } else if decision == "require_approval" {
        approvals.join("; ")
    } else {
        "runtime_state_store_policy_satisfied".to_string()
    };

    json!({
        "ok": decision != "block",
        "decision": decision,
        "reason": reason,
        "operation": operation,
        "next_action": next_action(&operation, decision),
        "approval_required": decision == "require_approval",
        "cacheable": false,
        "audit_visibility": if decision == "allow" { "standard" } else { "security" },
        "state": {
            "class": state_class,
            "storage_engine": storage_engine,
            "status": status,
            "previous_status": previous_status,
            "payload_present": payload_present,
            "payload_bytes": payload_bytes,
            "max_payload_bytes": max_payload_bytes,
        },
        "scope": {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "actor_id": actor_id,
            "workspace_access": workspace_access,
            "owner_access": owner_access,
        },
        "ids": {
            "run_id": run_id,
            "session_id": session_id,
            "turn_id": turn_id,
            "runtime_id": runtime_id,
            "notification_id": notification_id,
            "device_id": device_id,
            "trace_id": trace_id,
        },
        "versioning": {
            "expected_version": expected_version,
            "current_version": current_version,
            "version_checked": expected_version.is_some() || current_version.is_some(),
        },
        "retention": {
            "retention_days": retention_days,
            "retention_lock": retention_lock,
            "records_requested": records_requested,
            "max_records": max_records,
        },
        "normalized_flags": flags,
        "block_reasons": blocks,
        "approval_reasons": approvals,
    })
}

fn text(payload: &Value, keys: &[&str]) -> String {
    for key in keys {
        if let Some(Value::String(value)) = payload.get(*key) {
            let trimmed = value.trim();
            if !trimmed.is_empty() {
                return trimmed.to_string();
            }
        }
    }
    String::new()
}

fn flag(payload: &Value, keys: &[&str], default: bool) -> bool {
    for key in keys {
        if let Some(value) = payload.get(*key) {
            if let Some(flag) = value.as_bool() {
                return flag;
            }
            if let Some(text) = value.as_str() {
                return matches!(
                    normalize(text).as_str(),
                    "1" | "true" | "yes" | "on" | "enabled" | "valid" | "allow"
                );
            }
            if let Some(number) = value.as_i64() {
                return number > 0;
            }
        }
    }
    default
}

fn number(payload: &Value, keys: &[&str], default: i64) -> i64 {
    for key in keys {
        if let Some(value) = payload.get(*key) {
            if let Some(number) = value.as_i64() {
                return number;
            }
            if let Some(text) = value.as_str() {
                if let Ok(parsed) = text.trim().parse::<i64>() {
                    return parsed;
                }
            }
        }
    }
    default
}

fn opt_i64(payload: &Value, keys: &[&str]) -> Option<i64> {
    for key in keys {
        if let Some(value) = payload.get(*key) {
            if let Some(number) = value.as_i64() {
                return Some(number);
            }
            if let Some(text) = value.as_str() {
                if let Ok(parsed) = text.trim().parse::<i64>() {
                    return Some(parsed);
                }
            }
        }
    }
    None
}

fn present(payload: &Value, keys: &[&str]) -> bool {
    for key in keys {
        if let Some(value) = payload.get(*key) {
            match value {
                Value::Null => {}
                Value::String(text) => {
                    if !text.trim().is_empty() {
                        return true;
                    }
                }
                Value::Array(items) => {
                    if !items.is_empty() {
                        return true;
                    }
                }
                Value::Object(map) => {
                    if !map.is_empty() {
                        return true;
                    }
                }
                _ => return true,
            }
        }
    }
    false
}

fn normalize(value: &str) -> String {
    value
        .trim()
        .to_ascii_lowercase()
        .replace('-', "_")
        .replace(' ', "_")
}

fn normalize_or(value: &str, fallback: &str) -> String {
    let token = normalize(value);
    if token.is_empty() {
        fallback.to_string()
    } else {
        token
    }
}

fn normalize_operation(value: &str) -> String {
    match normalize(value).as_str() {
        "schema" | "init" | "init_schema" => "init_schema".to_string(),
        "live_run" | "upsert_live_run" => "upsert_live_run".to_string(),
        "delete_live_run" | "live_run_delete" => "delete_live_run".to_string(),
        "archive" | "archive_run" => "archive_run".to_string(),
        "runtime_registration" | "upsert_runtime_registration" => {
            "upsert_runtime_registration".to_string()
        }
        "fleet_queue_partition" | "upsert_fleet_queue_partition" => {
            "upsert_fleet_queue_partition".to_string()
        }
        "runtime_session" | "upsert_runtime_session" => "upsert_runtime_session".to_string(),
        "delete_session" | "delete_runtime_session" => "delete_runtime_session".to_string(),
        "runtime_turn" | "upsert_runtime_session_turn" => "upsert_runtime_session_turn".to_string(),
        "delete_turn" | "delete_runtime_session_turn" => "delete_runtime_session_turn".to_string(),
        "chat_stream" | "upsert_chat_stream_state" => "upsert_chat_stream_state".to_string(),
        "channel_event" | "append_channel_event" => "append_channel_event".to_string(),
        "claim" | "upsert_local_claim" => "upsert_local_claim".to_string(),
        "release_claim" | "release_local_claim" => "release_local_claim".to_string(),
        "notification" | "upsert_notification" => "upsert_notification".to_string(),
        "mark_read" | "mark_notification_read" => "mark_notification_read".to_string(),
        "notification_device" | "register_notification_device" => {
            "register_notification_device".to_string()
        }
        "notification_delivery" | "update_notification_delivery" => {
            "update_notification_delivery".to_string()
        }
        "set_kill_switch" | "kill_switch_set" => "set_kill_switch".to_string(),
        "clear_kill_switch" | "kill_switch_clear" => "clear_kill_switch".to_string(),
        "set_runtime_tool_enabled" | "runtime_tool_enabled_set" => {
            "set_runtime_tool_enabled".to_string()
        }
        "upsert_agent_computer_policy" | "agent_computer_policy_upsert" => {
            "upsert_agent_computer_policy".to_string()
        }
        "set_safe_mode_state" | "safe_mode_state_set" => "set_safe_mode_state".to_string(),
        "upsert_security_control_state" | "security_control_state_upsert" => {
            "upsert_security_control_state".to_string()
        }
        "upsert_sage_profile" | "sage_profile_upsert" => "upsert_sage_profile".to_string(),
        "save_mcp_server_registry" | "mcp_server_registry_save" => {
            "save_mcp_server_registry".to_string()
        }
        "save_installed_skill_registry" | "installed_skill_registry_save" => {
            "save_installed_skill_registry".to_string()
        }
        "write_vault_key_file" | "vault_key_file_write" => "write_vault_key_file".to_string(),
        "write_vault_blob_backup" | "vault_blob_backup_write" => "write_vault_blob_backup".to_string(),
        "read_vault_blob_backup" | "vault_blob_backup_read" => "read_vault_blob_backup".to_string(),
        "write_channel_pairings_backup" | "channel_pairings_backup_write" => "write_channel_pairings_backup".to_string(),
        "write_jwt_secret_file" | "jwt_secret_file_write" => "write_jwt_secret_file".to_string(),
        "persist_artifact_record" | "artifact_record_persist" => {
            "persist_artifact_record".to_string()
        }
        "write_hosted_sandbox_base_image" | "hosted_sandbox_base_image_write" => {
            "write_hosted_sandbox_base_image".to_string()
        }
        "save_cli_companion_state" | "cli_companion_state_save" => {
            "save_cli_companion_state".to_string()
        }
        "write_hosted_worker_output" | "hosted_worker_output_write" => {
            "write_hosted_worker_output".to_string()
        }
        "save_marketplace_distribution_state" | "marketplace_distribution_state_save" => {
            "save_marketplace_distribution_state".to_string()
        }
        "write_sage_dreaming_memory_state" | "sage_dreaming_memory_state_write" => {
            "write_sage_dreaming_memory_state".to_string()
        }
        "write_sage_dreaming_staging_file" | "sage_dreaming_staging_file_write" => {
            "write_sage_dreaming_staging_file".to_string()
        }
        "save_mini_apps_state" | "mini_apps_state_save" => "save_mini_apps_state".to_string(),
        "initialize_workspace_context_file" | "workspace_context_file_initialize" => {
            "initialize_workspace_context_file".to_string()
        }
        "save_workspace_context_file" | "workspace_context_file_save" => {
            "save_workspace_context_file".to_string()
        }
        "save_agent_computer_profile_state" | "agent_computer_profile_state_save" => {
            "save_agent_computer_profile_state".to_string()
        }
        "write_profile_api_file" | "profile_api_file_write" => "write_profile_api_file".to_string(),
        "write_runtime_common_json" | "runtime_common_json_write" => {
            "write_runtime_common_json".to_string()
        }
        "write_acp_manager_json" | "acp_manager_json_write" => "write_acp_manager_json".to_string(),
        "write_session_diagnostics_file" | "session_diagnostics_file_write" => {
            "write_session_diagnostics_file".to_string()
        }
        "append_agent_memory_daily_log" | "agent_memory_daily_log_append" => {
            "append_agent_memory_daily_log".to_string()
        }
        "write_no_provider_summary" | "no_provider_summary_write" => {
            "write_no_provider_summary".to_string()
        }
        "write_public_bot_drill_report" | "public_bot_drill_report_write" => {
            "write_public_bot_drill_report".to_string()
        }
        "write_telegram_media_file" | "telegram_media_file_write" => {
            "write_telegram_media_file".to_string()
        }
        "write_machine_capability_probe_file" | "machine_capability_probe_file_write" => {
            "write_machine_capability_probe_file".to_string()
        }
        "write_dropbox_download_file" | "dropbox_download_file_write" => {
            "write_dropbox_download_file".to_string()
        }
        "write_generated_image_file" | "generated_image_file_write" => {
            "write_generated_image_file".to_string()
        }
        "write_telegram_poll_lock_file" | "telegram_poll_lock_file_write" => {
            "write_telegram_poll_lock_file".to_string()
        }
        "write_skills_registry_file" | "skills_registry_file_write" => {
            "write_skills_registry_file".to_string()
        }
        "write_deployed_agent_knowledge_file" | "deployed_agent_knowledge_file_write" => {
            "write_deployed_agent_knowledge_file".to_string()
        }
        "write_outcome_pack_spreadsheet_file" | "outcome_pack_spreadsheet_file_write" => {
            "write_outcome_pack_spreadsheet_file".to_string()
        }
        "write_outcome_pack_document_file" | "outcome_pack_document_file_write" => {
            "write_outcome_pack_document_file".to_string()
        }
        "write_outcome_pack_remote_sync_file" | "outcome_pack_remote_sync_file_write" => {
            "write_outcome_pack_remote_sync_file".to_string()
        }
        "write_artifact_content_file" | "artifact_content_file_write" => {
            "write_artifact_content_file".to_string()
        }
        "execute_external_write_once" | "external_write_execute" => {
            "execute_external_write_once".to_string()
        }
        "create_gateway_tool_approval" | "gateway_tool_approval_create" => {
            "create_gateway_tool_approval".to_string()
        }
        "resolve_gateway_tool_approval" | "gateway_tool_approval_resolve" => {
            "resolve_gateway_tool_approval".to_string()
        }
        "expire_gateway_tool_approval" | "gateway_tool_approval_expire" => {
            "expire_gateway_tool_approval".to_string()
        }
        "fail_gateway_tool_approval" | "gateway_tool_approval_fail" => {
            "fail_gateway_tool_approval".to_string()
        }
        "execute_gateway_tool_approval" | "gateway_tool_approval_execute" => {
            "execute_gateway_tool_approval".to_string()
        }
        "send_gateway_protocol_request_frame" | "gateway_protocol_request_frame_send" => {
            "send_gateway_protocol_request_frame".to_string()
        }
        "acquire_channel_execution_lease" | "channel_execution_lease_acquire" => {
            "acquire_channel_execution_lease".to_string()
        }
        "release_channel_execution_lease" | "channel_execution_lease_release" => {
            "release_channel_execution_lease".to_string()
        }
        "start_direct_tool_execution" | "direct_tool_execution_start" => {
            "start_direct_tool_execution".to_string()
        }
        "block_direct_tool_execution" | "direct_tool_execution_block" => {
            "block_direct_tool_execution".to_string()
        }
        "fail_direct_tool_execution" | "direct_tool_execution_fail" => {
            "fail_direct_tool_execution".to_string()
        }
        "complete_direct_tool_execution" | "direct_tool_execution_complete" => {
            "complete_direct_tool_execution".to_string()
        }
        "upsert_workspace_memory" | "workspace_memory_upsert" => {
            "upsert_workspace_memory".to_string()
        }
        "delete_workspace_memory" | "workspace_memory_delete" => {
            "delete_workspace_memory".to_string()
        }
        "append_workspace_daily_log" | "workspace_daily_log_append" => {
            "append_workspace_daily_log".to_string()
        }
        "update_workspace_context_file" | "workspace_context_file_update" => {
            "update_workspace_context_file".to_string()
        }
        "upsert_approval_memory_rule" | "approval_memory_rule" => {
            "upsert_approval_memory_rule".to_string()
        }
        "consume_approval_memory_rule" | "approval_memory_consume" => {
            "consume_approval_memory_rule".to_string()
        }
        "create_sage_approval" | "sage_approval_create" => "create_sage_approval".to_string(),
        "resolve_sage_approval" | "sage_approval_resolve" => "resolve_sage_approval".to_string(),
        "consume_sage_approval" | "sage_approval_consume" => "consume_sage_approval".to_string(),
        "expire_sage_approvals" | "sage_approval_expire" => "expire_sage_approvals".to_string(),
        "update_sage_service_profile" | "sage_service_profile_update" => {
            "update_sage_service_profile".to_string()
        }
        "create_sage_service_entry" | "sage_service_entry_create" => {
            "create_sage_service_entry".to_string()
        }
        "update_sage_service_entry" | "sage_service_entry_update" => {
            "update_sage_service_entry".to_string()
        }
        "delete_sage_service_entry" | "sage_service_entry_delete" => {
            "delete_sage_service_entry".to_string()
        }
        "set_sage_service_entry_pinned" | "sage_service_entry_pin" => {
            "set_sage_service_entry_pinned".to_string()
        }
        "upsert_sage_memory_entry" | "sage_memory_upsert" => "upsert_sage_memory_entry".to_string(),
        "update_sage_memory_entry" | "sage_memory_update" => "update_sage_memory_entry".to_string(),
        "delete_sage_memory_entry" | "sage_memory_delete" => "delete_sage_memory_entry".to_string(),
        "wipe_sage_memory" | "sage_memory_wipe" => "wipe_sage_memory".to_string(),
        "append_session_transcript" | "session_transcript_append" => {
            "append_session_transcript".to_string()
        }
        "append_activity_ledger_event" | "activity_ledger_append" => {
            "append_activity_ledger_event".to_string()
        }
        "write_shared_operational_board_entry" | "shared_operational_board_write" => {
            "write_shared_operational_board_entry".to_string()
        }
        "checkpoint" | "checkpoint_snapshot" => "checkpoint_snapshot".to_string(),
        "prune" | "prune_records" => "prune_records".to_string(),
        "" => String::new(),
        other => other.to_string(),
    }
}

fn default_state_class(operation: &str) -> &str {
    match operation {
        "upsert_live_run" | "delete_live_run" => "live_runs",
        "archive_run" => "run_archive",
        "create_or_update_approval_request"
        | "resolve_approval_if_pending"
        | "record_approval_resolution" => "run_approvals",
        "upsert_runtime_registration" => "runtime_registrations",
        "upsert_fleet_queue_partition" => "fleet_queue_partitions",
        "upsert_runtime_session" | "delete_runtime_session" => "runtime_sessions",
        "upsert_runtime_session_turn" | "delete_runtime_session_turn" => "runtime_session_turns",
        "upsert_chat_stream_state" => "chat_stream_state",
        "append_channel_event" => "channel_events",
        "append_session_transcript" => "session_transcripts",
        "append_activity_ledger_event" => "activity_ledger",
        "upsert_local_claim" | "release_local_claim" => "local_claims",
        "upsert_notification" | "mark_notification_read" => "notifications",
        "register_notification_device" => "notification_devices",
        "update_notification_delivery" => "notification_delivery_statuses",
        "set_kill_switch" | "clear_kill_switch" => "kill_switches",
        "set_runtime_tool_enabled" => "runtime_tool_policy",
        "upsert_agent_computer_policy" => "agent_computer_policy",
        "set_safe_mode_state" | "upsert_security_control_state" => "security_control_state",
        "upsert_sage_profile" => "sage_profile",
        "save_mcp_server_registry" => "mcp_server_registry",
        "save_installed_skill_registry" => "installed_skill_registry",
        "write_vault_key_file" | "write_vault_blob_backup" | "read_vault_blob_backup" | "write_channel_pairings_backup" | "write_jwt_secret_file" => "secret_material",
        "persist_artifact_record" => "artifact_records",
        "write_hosted_sandbox_base_image" => "execution_sandbox_image",
        "save_cli_companion_state" => "cli_companion_state",
        "write_hosted_worker_output" => "hosted_worker_output",
        "save_marketplace_distribution_state" => "marketplace_distribution",
        "write_sage_dreaming_memory_state" | "write_sage_dreaming_staging_file" => "sage_dreaming",
        "save_mini_apps_state" => "mini_apps",
        "initialize_workspace_context_file" | "save_workspace_context_file" => {
            "workspace_context_files"
        }
        "save_agent_computer_profile_state" => "agent_computer_profiles",
        "write_profile_api_file" => "profile_api_files",
        "write_runtime_common_json" => "runtime_common_json_files",
        "write_acp_manager_json" => "acp_manager_state",
        "write_session_diagnostics_file" => "session_diagnostics_files",
        "append_agent_memory_daily_log" => "agent_memory_daily_logs",
        "write_no_provider_summary" => "no_provider_summaries",
        "write_public_bot_drill_report" => "public_bot_drill_reports",
        "write_telegram_media_file" => "telegram_media_files",
        "write_machine_capability_probe_file" => "machine_capability_probe",
        "write_dropbox_download_file" => "dropbox_download_files",
        "write_generated_image_file" => "generated_image_files",
        "write_telegram_poll_lock_file" => "telegram_poll_lock_files",
        "write_skills_registry_file" => "skills_registry_files",
        "write_deployed_agent_knowledge_file" => "deployed_agent_knowledge_files",
        "write_outcome_pack_spreadsheet_file" => "outcome_pack_spreadsheet_files",
        "write_outcome_pack_document_file" => "outcome_pack_document_files",
        "write_outcome_pack_remote_sync_file" => "outcome_pack_remote_sync_files",
        "write_artifact_content_file" => "artifact_content_files",
        "execute_external_write_once" => "external_write_executions",
        "create_gateway_tool_approval"
        | "resolve_gateway_tool_approval"
        | "expire_gateway_tool_approval"
        | "fail_gateway_tool_approval"
        | "execute_gateway_tool_approval" => "gateway_approvals",
        "send_gateway_protocol_request_frame" => "gateway_protocol_frames",
        "acquire_channel_execution_lease" | "release_channel_execution_lease" => {
            "channel_execution_leases"
        }
        "start_direct_tool_execution"
        | "block_direct_tool_execution"
        | "fail_direct_tool_execution"
        | "complete_direct_tool_execution" => "direct_tool_execution",
        "upsert_workspace_memory" | "delete_workspace_memory" | "append_workspace_daily_log" => {
            "workspace_memory"
        }
        "update_workspace_context_file" => "workspace_context_files",
        "upsert_approval_memory_rule" | "consume_approval_memory_rule" => "agent_approval_memory",
        "create_sage_approval"
        | "resolve_sage_approval"
        | "consume_sage_approval"
        | "expire_sage_approvals" => "sage_approvals",
        "update_sage_service_profile"
        | "create_sage_service_entry"
        | "update_sage_service_entry"
        | "delete_sage_service_entry"
        | "set_sage_service_entry_pinned" => "sage_services",
        "upsert_sage_memory_entry"
        | "update_sage_memory_entry"
        | "delete_sage_memory_entry"
        | "wipe_sage_memory" => "sage_memory",
        "write_shared_operational_board_entry" => "shared_operational_board",
        "checkpoint_snapshot" => "local_app_state",
        _ => "",
    }
}

fn requires_workspace(operation: &str) -> bool {
    !matches!(
        operation,
        "" | "init_schema"
            | "checkpoint_snapshot"
            | "set_kill_switch"
            | "clear_kill_switch"
            | "set_runtime_tool_enabled"
            | "set_safe_mode_state"
            | "save_mcp_server_registry"
            | "save_installed_skill_registry"
            | "write_vault_key_file"
            | "write_vault_blob_backup"
            | "read_vault_blob_backup"
            | "write_channel_pairings_backup"
            | "write_jwt_secret_file"
            | "write_hosted_sandbox_base_image"
            | "write_hosted_worker_output"
            | "save_cli_companion_state"
            | "write_profile_api_file"
            | "write_runtime_common_json"
            | "write_acp_manager_json"
            | "write_session_diagnostics_file"
            | "write_no_provider_summary"
            | "write_public_bot_drill_report"
            | "write_telegram_media_file"
            | "write_machine_capability_probe_file"
            | "write_dropbox_download_file"
            | "write_generated_image_file"
            | "write_telegram_poll_lock_file"
            | "write_skills_registry_file"
            | "write_outcome_pack_spreadsheet_file"
            | "write_outcome_pack_document_file"
            | "write_outcome_pack_remote_sync_file"
    )
}

fn requires_owner_access(operation: &str) -> bool {
    matches!(
        operation,
        "delete_runtime_session"
            | "delete_runtime_session_turn"
            | "prune_records"
            | "register_notification_device"
            | "set_kill_switch"
            | "clear_kill_switch"
            | "set_runtime_tool_enabled"
            | "upsert_agent_computer_policy"
            | "set_safe_mode_state"
            | "upsert_security_control_state"
            | "upsert_sage_profile"
            | "save_mcp_server_registry"
            | "save_installed_skill_registry"
            | "write_vault_key_file"
            | "write_vault_blob_backup"
            | "read_vault_blob_backup"
            | "write_channel_pairings_backup"
            | "write_jwt_secret_file"
            | "persist_artifact_record"
            | "write_hosted_sandbox_base_image"
            | "save_cli_companion_state"
            | "write_hosted_worker_output"
            | "save_marketplace_distribution_state"
            | "write_sage_dreaming_memory_state"
            | "write_sage_dreaming_staging_file"
            | "save_mini_apps_state"
            | "initialize_workspace_context_file"
            | "save_workspace_context_file"
            | "save_agent_computer_profile_state"
            | "write_profile_api_file"
            | "write_runtime_common_json"
            | "write_acp_manager_json"
            | "write_session_diagnostics_file"
            | "append_agent_memory_daily_log"
            | "write_no_provider_summary"
            | "write_public_bot_drill_report"
            | "write_telegram_media_file"
            | "write_machine_capability_probe_file"
            | "write_dropbox_download_file"
            | "write_generated_image_file"
            | "write_telegram_poll_lock_file"
            | "write_skills_registry_file"
            | "write_deployed_agent_knowledge_file"
            | "write_outcome_pack_spreadsheet_file"
            | "write_outcome_pack_document_file"
            | "write_outcome_pack_remote_sync_file"
            | "write_artifact_content_file"
            | "execute_external_write_once"
            | "create_gateway_tool_approval"
            | "resolve_gateway_tool_approval"
            | "expire_gateway_tool_approval"
            | "fail_gateway_tool_approval"
            | "execute_gateway_tool_approval"
            | "send_gateway_protocol_request_frame"
            | "acquire_channel_execution_lease"
            | "release_channel_execution_lease"
            | "start_direct_tool_execution"
            | "block_direct_tool_execution"
            | "fail_direct_tool_execution"
            | "complete_direct_tool_execution"
            | "upsert_workspace_memory"
            | "delete_workspace_memory"
            | "append_workspace_daily_log"
            | "update_workspace_context_file"
            | "upsert_approval_memory_rule"
            | "consume_approval_memory_rule"
            | "create_sage_approval"
            | "resolve_sage_approval"
            | "consume_sage_approval"
            | "expire_sage_approvals"
            | "update_sage_service_profile"
            | "create_sage_service_entry"
            | "update_sage_service_entry"
            | "delete_sage_service_entry"
            | "set_sage_service_entry_pinned"
            | "upsert_sage_memory_entry"
            | "update_sage_memory_entry"
            | "delete_sage_memory_entry"
            | "wipe_sage_memory"
            | "append_activity_ledger_event"
            | "write_shared_operational_board_entry"
    )
}

fn requires_payload(operation: &str) -> bool {
    matches!(
        operation,
        "upsert_live_run"
            | "archive_run"
            | "create_or_update_approval_request"
            | "resolve_approval_if_pending"
            | "record_approval_resolution"
            | "upsert_runtime_registration"
            | "upsert_runtime_session"
            | "upsert_runtime_session_turn"
            | "upsert_chat_stream_state"
            | "append_channel_event"
            | "upsert_local_claim"
            | "upsert_notification"
            | "register_notification_device"
            | "update_notification_delivery"
            | "set_kill_switch"
            | "clear_kill_switch"
            | "set_runtime_tool_enabled"
            | "upsert_agent_computer_policy"
            | "set_safe_mode_state"
            | "upsert_security_control_state"
            | "upsert_sage_profile"
            | "save_mcp_server_registry"
            | "save_installed_skill_registry"
            | "write_vault_key_file"
            | "write_vault_blob_backup"
            | "write_channel_pairings_backup"
            | "write_jwt_secret_file"
            | "persist_artifact_record"
            | "write_hosted_sandbox_base_image"
            | "save_cli_companion_state"
            | "write_hosted_worker_output"
            | "save_marketplace_distribution_state"
            | "write_sage_dreaming_memory_state"
            | "write_sage_dreaming_staging_file"
            | "save_mini_apps_state"
            | "initialize_workspace_context_file"
            | "save_workspace_context_file"
            | "save_agent_computer_profile_state"
            | "write_profile_api_file"
            | "write_runtime_common_json"
            | "write_acp_manager_json"
            | "write_session_diagnostics_file"
            | "append_agent_memory_daily_log"
            | "write_no_provider_summary"
            | "write_public_bot_drill_report"
            | "write_telegram_media_file"
            | "write_machine_capability_probe_file"
            | "write_dropbox_download_file"
            | "write_generated_image_file"
            | "write_telegram_poll_lock_file"
            | "write_skills_registry_file"
            | "write_deployed_agent_knowledge_file"
            | "write_outcome_pack_spreadsheet_file"
            | "write_outcome_pack_document_file"
            | "write_outcome_pack_remote_sync_file"
            | "write_artifact_content_file"
            | "execute_external_write_once"
            | "create_gateway_tool_approval"
            | "resolve_gateway_tool_approval"
            | "expire_gateway_tool_approval"
            | "fail_gateway_tool_approval"
            | "execute_gateway_tool_approval"
            | "send_gateway_protocol_request_frame"
            | "acquire_channel_execution_lease"
            | "release_channel_execution_lease"
            | "start_direct_tool_execution"
            | "block_direct_tool_execution"
            | "fail_direct_tool_execution"
            | "complete_direct_tool_execution"
            | "upsert_workspace_memory"
            | "delete_workspace_memory"
            | "append_workspace_daily_log"
            | "update_workspace_context_file"
            | "upsert_approval_memory_rule"
            | "consume_approval_memory_rule"
            | "create_sage_approval"
            | "resolve_sage_approval"
            | "consume_sage_approval"
            | "expire_sage_approvals"
            | "update_sage_service_profile"
            | "create_sage_service_entry"
            | "update_sage_service_entry"
            | "delete_sage_service_entry"
            | "set_sage_service_entry_pinned"
            | "upsert_sage_memory_entry"
            | "update_sage_memory_entry"
            | "delete_sage_memory_entry"
            | "wipe_sage_memory"
            | "append_session_transcript"
            | "append_activity_ledger_event"
            | "write_shared_operational_board_entry"
            | "checkpoint_snapshot"
    )
}

fn requires_run_id(operation: &str, state_class: &str) -> bool {
    matches!(
        operation,
        "upsert_live_run" | "archive_run" | "upsert_local_claim" | "release_local_claim"
    ) || matches!(
        state_class,
        "live_runs" | "run_archive" | "local_claims" | "run_approvals"
    )
}

fn requires_session_id(operation: &str, state_class: &str) -> bool {
    matches!(
        operation,
        "upsert_runtime_session" | "delete_runtime_session" | "upsert_runtime_session_turn"
    ) || state_class == "runtime_sessions"
}

fn requires_turn_id(operation: &str, state_class: &str) -> bool {
    matches!(
        operation,
        "upsert_runtime_session_turn" | "delete_runtime_session_turn"
    ) || state_class == "runtime_session_turns"
}

fn requires_runtime_id(operation: &str, state_class: &str) -> bool {
    matches!(operation, "upsert_runtime_registration") || state_class == "runtime_registrations"
}

fn version_conflict(expected: Option<i64>, current: Option<i64>) -> bool {
    matches!((expected, current), (Some(expected), Some(current)) if expected != current)
}

fn safe_delete_status(status: &str) -> bool {
    // MAN-108 Bug 2: same Python/Rust terminal-vocabulary gap as
    // TERMINAL_RUN_STATUSES above -- this list never recognized "completed"
    // (or "timeout"/"cancelled"/"stopped") either, so remove_live_run_state
    // (delete_live_run, called right after archive_run for every completed
    // run) hit destructive_state_operation_requires_approval for every
    // durable run that ever finished successfully. Same fix, same
    // reasoning: purely additive, only widens what's already considered a
    // safe delete.
    matches!(
        status,
        "closed"
            | "expired"
            | "failed"
            | "terminated"
            | "archived"
            | "completed"
            | "timeout"
            | "cancelled"
            | "canceled"
            | "stopped"
    )
}

fn next_action(operation: &str, decision: &str) -> &'static str {
    if decision == "block" {
        return "stop_state_store_write";
    }
    if decision == "require_approval" {
        return "request_state_store_approval";
    }
    match operation {
        "init_schema" => "initialize_state_schema",
        "upsert_live_run" => "write_live_run_state",
        "delete_live_run" => "delete_live_run_state",
        "archive_run" => "write_run_archive",
        "create_or_update_approval_request" => "write_run_approval_request",
        "resolve_approval_if_pending" => "resolve_run_approval",
        "record_approval_resolution" => "record_run_approval_resolution",
        "upsert_runtime_registration" => "write_runtime_registration",
        "upsert_fleet_queue_partition" => "write_fleet_queue_partition",
        "upsert_runtime_session" => "write_runtime_session",
        "delete_runtime_session" => "delete_runtime_session",
        "upsert_runtime_session_turn" => "write_runtime_session_turn",
        "delete_runtime_session_turn" => "delete_runtime_session_turn",
        "upsert_chat_stream_state" => "write_chat_stream_state",
        "append_channel_event" => "append_channel_event",
        "upsert_local_claim" => "write_local_claim",
        "release_local_claim" => "release_local_claim",
        "upsert_notification" => "write_notification",
        "mark_notification_read" => "mark_notification_read",
        "register_notification_device" => "write_notification_device",
        "update_notification_delivery" => "write_notification_delivery",
        "set_kill_switch" => "set_kill_switch",
        "clear_kill_switch" => "clear_kill_switch",
        "set_runtime_tool_enabled" => "set_runtime_tool_enabled",
        "upsert_agent_computer_policy" => "upsert_agent_computer_policy",
        "set_safe_mode_state" => "set_safe_mode_state",
        "upsert_security_control_state" => "upsert_security_control_state",
        "upsert_sage_profile" => "upsert_sage_profile",
        "save_mcp_server_registry" => "save_mcp_server_registry",
        "save_installed_skill_registry" => "save_installed_skill_registry",
        "write_vault_key_file" => "write_vault_key_file",
        "write_vault_blob_backup" => "write_vault_blob_backup",
        "read_vault_blob_backup" => "read_vault_blob_backup",
        "write_channel_pairings_backup" => "write_channel_pairings_backup",
        "write_jwt_secret_file" => "write_jwt_secret_file",
        "persist_artifact_record" => "persist_artifact_record",
        "write_hosted_sandbox_base_image" => "write_hosted_sandbox_base_image",
        "save_cli_companion_state" => "save_cli_companion_state",
        "write_hosted_worker_output" => "write_hosted_worker_output",
        "save_marketplace_distribution_state" => "save_marketplace_distribution_state",
        "write_sage_dreaming_memory_state" => "write_sage_dreaming_memory_state",
        "write_sage_dreaming_staging_file" => "write_sage_dreaming_staging_file",
        "save_mini_apps_state" => "save_mini_apps_state",
        "initialize_workspace_context_file" => "initialize_workspace_context_file",
        "save_workspace_context_file" => "save_workspace_context_file",
        "save_agent_computer_profile_state" => "save_agent_computer_profile_state",
        "write_profile_api_file" => "write_profile_api_file",
        "write_runtime_common_json" => "write_runtime_common_json",
        "write_acp_manager_json" => "write_acp_manager_json",
        "write_session_diagnostics_file" => "write_session_diagnostics_file",
        "append_agent_memory_daily_log" => "append_agent_memory_daily_log",
        "write_no_provider_summary" => "write_no_provider_summary",
        "write_public_bot_drill_report" => "write_public_bot_drill_report",
        "write_telegram_media_file" => "write_telegram_media_file",
        "write_machine_capability_probe_file" => "write_machine_capability_probe_file",
        "write_dropbox_download_file" => "write_dropbox_download_file",
        "write_generated_image_file" => "write_generated_image_file",
        "write_telegram_poll_lock_file" => "write_telegram_poll_lock_file",
        "write_skills_registry_file" => "write_skills_registry_file",
        "write_deployed_agent_knowledge_file" => "write_deployed_agent_knowledge_file",
        "write_outcome_pack_spreadsheet_file" => "write_outcome_pack_spreadsheet_file",
        "write_outcome_pack_document_file" => "write_outcome_pack_document_file",
        "write_outcome_pack_remote_sync_file" => "write_outcome_pack_remote_sync_file",
        "write_artifact_content_file" => "write_artifact_content_file",
        "execute_external_write_once" => "execute_external_write_once",
        "create_gateway_tool_approval" => "create_gateway_tool_approval",
        "resolve_gateway_tool_approval" => "resolve_gateway_tool_approval",
        "expire_gateway_tool_approval" => "expire_gateway_tool_approval",
        "fail_gateway_tool_approval" => "fail_gateway_tool_approval",
        "execute_gateway_tool_approval" => "execute_gateway_tool_approval",
        "send_gateway_protocol_request_frame" => "send_gateway_protocol_request_frame",
        "acquire_channel_execution_lease" => "acquire_channel_execution_lease",
        "release_channel_execution_lease" => "release_channel_execution_lease",
        "start_direct_tool_execution" => "start_direct_tool_execution",
        "block_direct_tool_execution" => "block_direct_tool_execution",
        "fail_direct_tool_execution" => "fail_direct_tool_execution",
        "complete_direct_tool_execution" => "complete_direct_tool_execution",
        "upsert_workspace_memory" => "write_workspace_memory",
        "delete_workspace_memory" => "delete_workspace_memory",
        "append_workspace_daily_log" => "append_workspace_daily_log",
        "update_workspace_context_file" => "write_workspace_context_file",
        "upsert_approval_memory_rule" => "write_approval_memory_rule",
        "consume_approval_memory_rule" => "consume_approval_memory_rule",
        "create_sage_approval" => "create_sage_approval",
        "resolve_sage_approval" => "resolve_sage_approval",
        "consume_sage_approval" => "consume_sage_approval",
        "expire_sage_approvals" => "expire_sage_approvals",
        "update_sage_service_profile" => "update_sage_service_profile",
        "create_sage_service_entry" => "create_sage_service_entry",
        "update_sage_service_entry" => "update_sage_service_entry",
        "delete_sage_service_entry" => "delete_sage_service_entry",
        "set_sage_service_entry_pinned" => "set_sage_service_entry_pinned",
        "upsert_sage_memory_entry" => "write_sage_memory_entry",
        "update_sage_memory_entry" => "update_sage_memory_entry",
        "delete_sage_memory_entry" => "delete_sage_memory_entry",
        "wipe_sage_memory" => "wipe_sage_memory",
        "append_session_transcript" => "append_session_transcript",
        "append_activity_ledger_event" => "append_activity_ledger_event",
        "write_shared_operational_board_entry" => "write_shared_operational_board_entry",
        "checkpoint_snapshot" => "write_checkpoint_snapshot",
        "prune_records" => "prune_state_records",
        _ => "review_state_store_write",
    }
}

#[cfg(test)]
mod runtime_state_store_workspace_context_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn delete_workspace_memory_requires_owner_access() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "delete_workspace_memory",
            "state_class": "workspace_memory",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "member-1",
            "status": "archived",
            "payload": {"key": "memory-key"},
            "workspace_access": true,
            "owner_access": false,
            "destructive_approval": true
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "owner_access_denied");
    }

    #[test]
    fn update_workspace_context_file_allows_owner_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "update_workspace_context_file",
            "state_class": "workspace_context_files",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "owner-1",
            "status": "active",
            "payload": {"filename": "notes.md", "content": "updated"},
            "payload_bytes": 7,
            "workspace_access": true,
            "owner_access": true,
            "destructive_approval": true
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "update_workspace_context_file");
        assert_eq!(decision["next_action"], "write_workspace_context_file");
    }
}

#[cfg(test)]
mod runtime_state_store_workspace_memory_destructive_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn delete_workspace_memory_respects_retention_lock() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "delete_workspace_memory",
            "state_class": "workspace_memory",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "owner-1",
            "status": "archived",
            "payload": {"key": "memory-key"},
            "workspace_access": true,
            "owner_access": true,
            "destructive_approval": true,
            "retention_lock": true
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "retention_lock_blocks_delete");
    }
}

#[cfg(test)]
mod runtime_state_store_approval_memory_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn approval_memory_upsert_requires_owner_access() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "upsert_approval_memory_rule",
            "state_class": "agent_approval_memory",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "member-1",
            "status": "active",
            "payload": {"rule_id": "amr_1", "capability": "browser.click"},
            "workspace_access": true,
            "owner_access": false,
            "destructive_approval": true
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "owner_access_denied");
    }

    #[test]
    fn approval_memory_consume_allows_owner_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "consume_approval_memory_rule",
            "state_class": "agent_approval_memory",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "owner-1",
            "status": "active",
            "payload": {"rule_id": "amr_1", "use_count": 2},
            "workspace_access": true,
            "owner_access": true,
            "destructive_approval": true
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "consume_approval_memory_rule");
        assert_eq!(decision["next_action"], "consume_approval_memory_rule");
    }
}

#[cfg(test)]
mod runtime_state_store_sage_memory_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn sage_memory_upsert_requires_owner_access() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "upsert_sage_memory_entry",
            "state_class": "sage_memory",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "member-1",
            "status": "active",
            "payload": {"id": "entry-1", "category": "safe_general"},
            "workspace_access": true,
            "owner_access": false,
            "destructive_approval": true
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "owner_access_denied");
    }

    #[test]
    fn sage_memory_delete_respects_retention_lock() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "delete_sage_memory_entry",
            "state_class": "sage_memory",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "owner-1",
            "status": "archived",
            "payload": {"id": "entry-1", "category": "safe_general"},
            "workspace_access": true,
            "owner_access": true,
            "destructive_approval": true,
            "retention_lock": true
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "retention_lock_blocks_delete");
    }

    #[test]
    fn sage_memory_update_allows_owner_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "update_sage_memory_entry",
            "state_class": "sage_memory",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "owner-1",
            "status": "active",
            "payload": {"id": "entry-1", "pinned": true},
            "workspace_access": true,
            "owner_access": true,
            "destructive_approval": true
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "update_sage_memory_entry");
        assert_eq!(decision["next_action"], "update_sage_memory_entry");
    }
}

#[cfg(test)]
mod runtime_state_store_workspace_memory_core_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn workspace_memory_upsert_requires_payload() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "upsert_workspace_memory",
            "state_class": "workspace_memory",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "owner-1",
            "status": "active",
            "workspace_access": true,
            "owner_access": true,
            "destructive_approval": true
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "state_payload_missing");
    }

    #[test]
    fn workspace_daily_log_append_allows_owner_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "append_workspace_daily_log",
            "state_class": "workspace_memory",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "owner-1",
            "status": "active",
            "payload": {"content": "Durable project note"},
            "workspace_access": true,
            "owner_access": true,
            "destructive_approval": true
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "append_workspace_daily_log");
        assert_eq!(decision["next_action"], "append_workspace_daily_log");
    }
}

#[cfg(test)]
mod runtime_state_store_transcript_and_board_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn session_transcript_append_requires_workspace_access() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "append_session_transcript",
            "state_class": "session_transcripts",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "thread-1",
            "status": "active",
            "payload": {"thread_id": "thread-1", "messages": []},
            "workspace_access": false,
            "owner_access": true,
            "destructive_approval": true
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "workspace_access_denied");
    }

    #[test]
    fn shared_operational_board_write_requires_owner_access() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "write_shared_operational_board_entry",
            "state_class": "shared_operational_board",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "member-1",
            "status": "published",
            "payload": {"entry_id": "sboard_1", "title": "Handoff"},
            "workspace_access": true,
            "owner_access": false,
            "destructive_approval": true
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "owner_access_denied");
    }

    #[test]
    fn shared_operational_board_write_allows_owner_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "write_shared_operational_board_entry",
            "state_class": "shared_operational_board",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "owner-1",
            "status": "published",
            "payload": {"entry_id": "sboard_1", "title": "Handoff"},
            "workspace_access": true,
            "owner_access": true,
            "destructive_approval": true
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(
            decision["next_action"],
            "write_shared_operational_board_entry"
        );
    }
}

#[cfg(test)]
mod runtime_state_store_activity_ledger_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn activity_ledger_append_requires_owner_access() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "append_activity_ledger_event",
            "state_class": "activity_ledger",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "member-1",
            "status": "logged",
            "payload": {"event_class": "memory_update", "title": "Memory updated"},
            "workspace_access": true,
            "owner_access": false,
            "destructive_approval": true
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "owner_access_denied");
    }

    #[test]
    fn activity_ledger_append_allows_owner_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "append_activity_ledger_event",
            "state_class": "activity_ledger",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "owner-1",
            "status": "logged",
            "payload": {"event_class": "memory_update", "title": "Memory updated"},
            "workspace_access": true,
            "owner_access": true,
            "destructive_approval": true
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["next_action"], "append_activity_ledger_event");
    }
}

#[cfg(test)]
mod runtime_state_store_sage_approval_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn create_sage_approval_requires_owner_access() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "create_sage_approval",
            "workspace_id": "workspace-1",
            "owner_access": false,
            "payload": {"approval_token": "sap_1", "status": "pending"}
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "create_sage_approval");
        assert_eq!(decision["state"]["class"], "sage_approvals");
        assert_eq!(decision["reason"], "owner_access_denied");
    }

    #[test]
    fn resolve_sage_approval_allows_owner_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "sage_approval_resolve",
            "workspace_id": "workspace-1",
            "actor_id": "owner-1",
            "status": "approved",
            "payload": {"approval_token": "sap_1", "status": "approved"}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "resolve_sage_approval");
        assert_eq!(decision["next_action"], "resolve_sage_approval");
        assert_eq!(decision["approval_required"], false);
    }

    #[test]
    fn expire_sage_approvals_requires_payload() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "expire_sage_approvals",
            "workspace_id": "workspace-1",
            "status": "expired"
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "expire_sage_approvals");
        assert_eq!(decision["reason"], "state_payload_missing");
    }
}

#[cfg(test)]
mod runtime_state_store_sage_services_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn update_sage_service_profile_requires_owner_access() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "update_sage_service_profile",
            "workspace_id": "workspace-1",
            "owner_access": false,
            "payload": {"service_id": "flashcards", "profile": {"active_deck": "bio"}}
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "update_sage_service_profile");
        assert_eq!(decision["state"]["class"], "sage_services");
        assert_eq!(decision["reason"], "owner_access_denied");
    }

    #[test]
    fn create_sage_service_entry_allows_owner_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "sage_service_entry_create",
            "workspace_id": "workspace-1",
            "actor_id": "owner-1",
            "payload": {"service_id": "flashcards", "entry": {"front": "Q", "back": "A"}}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "create_sage_service_entry");
        assert_eq!(decision["state"]["class"], "sage_services");
        assert_eq!(decision["next_action"], "create_sage_service_entry");
    }

    #[test]
    fn delete_sage_service_entry_requires_payload() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "delete_sage_service_entry",
            "workspace_id": "workspace-1",
            "actor_id": "owner-1"
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "delete_sage_service_entry");
        assert_eq!(decision["reason"], "state_payload_missing");
    }
}

#[cfg(test)]
mod runtime_state_store_kill_switch_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn set_kill_switch_requires_owner_access_without_workspace() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "set_kill_switch",
            "owner_access": false,
            "payload": {"key": "global_pilot", "active": true}
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "set_kill_switch");
        assert_eq!(decision["state"]["class"], "kill_switches");
        assert_eq!(decision["reason"], "owner_access_denied");
    }

    #[test]
    fn clear_kill_switch_allows_owner_write_without_workspace() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "kill_switch_clear",
            "actor_id": "system",
            "status": "cleared",
            "payload": {"key": "gateway:local", "active": false}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "clear_kill_switch");
        assert_eq!(decision["state"]["class"], "kill_switches");
        assert_eq!(decision["scope"]["workspace_id"], "");
        assert_eq!(decision["next_action"], "clear_kill_switch");
    }

    #[test]
    fn set_kill_switch_requires_payload() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "set_kill_switch",
            "actor_id": "system"
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "set_kill_switch");
        assert_eq!(decision["reason"], "state_payload_missing");
    }
}

#[cfg(test)]
mod runtime_state_store_runtime_tool_policy_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn set_runtime_tool_enabled_requires_owner_access_without_workspace() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "set_runtime_tool_enabled",
            "owner_access": false,
            "payload": {"tool_id": "shell.execute", "enabled": false}
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "set_runtime_tool_enabled");
        assert_eq!(decision["state"]["class"], "runtime_tool_policy");
        assert_eq!(decision["reason"], "owner_access_denied");
    }

    #[test]
    fn set_runtime_tool_enabled_allows_owner_write_without_workspace() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "runtime_tool_enabled_set",
            "actor_id": "system",
            "status": "disabled",
            "payload": {"tool_id": "shell.execute", "enabled": false}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "set_runtime_tool_enabled");
        assert_eq!(decision["state"]["class"], "runtime_tool_policy");
        assert_eq!(decision["scope"]["workspace_id"], "");
        assert_eq!(decision["next_action"], "set_runtime_tool_enabled");
    }

    #[test]
    fn set_runtime_tool_enabled_requires_payload() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "set_runtime_tool_enabled",
            "actor_id": "system"
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "set_runtime_tool_enabled");
        assert_eq!(decision["reason"], "state_payload_missing");
    }
}

#[cfg(test)]
mod runtime_state_store_agent_computer_policy_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn upsert_agent_computer_policy_requires_workspace() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "upsert_agent_computer_policy",
            "payload": {"policy_id": "default", "policy": {"autonomy_mode": "ask_every_time"}}
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "upsert_agent_computer_policy");
        assert_eq!(decision["state"]["class"], "agent_computer_policy");
        assert_eq!(decision["reason"], "workspace_id_missing");
    }

    #[test]
    fn upsert_agent_computer_policy_requires_owner_access() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "agent_computer_policy_upsert",
            "workspace_id": "workspace-1",
            "owner_access": false,
            "payload": {"policy_id": "default", "policy": {"autonomy_mode": "ask_every_time"}}
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "upsert_agent_computer_policy");
        assert_eq!(decision["reason"], "owner_access_denied");
    }

    #[test]
    fn upsert_agent_computer_policy_allows_owner_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "upsert_agent_computer_policy",
            "workspace_id": "workspace-1",
            "actor_id": "system",
            "payload": {"policy_id": "default", "policy": {"autonomy_mode": "ask_every_time"}}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "upsert_agent_computer_policy");
        assert_eq!(decision["state"]["class"], "agent_computer_policy");
        assert_eq!(decision["next_action"], "upsert_agent_computer_policy");
    }
}

#[cfg(test)]
mod runtime_state_store_safe_mode_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn set_safe_mode_state_allows_global_owner_write_without_workspace() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "safe_mode_state_set",
            "actor_id": "system",
            "status": "active",
            "payload": {"scope": "global", "enabled": true, "control_kind": "safe_mode"}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "set_safe_mode_state");
        assert_eq!(decision["state"]["class"], "security_control_state");
        assert_eq!(decision["scope"]["workspace_id"], "");
        assert_eq!(decision["next_action"], "set_safe_mode_state");
    }

    #[test]
    fn set_safe_mode_state_requires_owner_access() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "set_safe_mode_state",
            "owner_access": false,
            "payload": {"scope": "workspace", "enabled": true, "control_kind": "safe_mode"}
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "set_safe_mode_state");
        assert_eq!(decision["reason"], "owner_access_denied");
    }

    #[test]
    fn upsert_security_control_state_requires_workspace() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "upsert_security_control_state",
            "payload": {"scope": "workspace", "enabled": true, "control_kind": "safe_mode"}
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "upsert_security_control_state");
        assert_eq!(decision["state"]["class"], "security_control_state");
        assert_eq!(decision["reason"], "workspace_id_missing");
    }
}

#[cfg(test)]
mod runtime_state_store_sage_profile_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn upsert_sage_profile_requires_workspace() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "upsert_sage_profile",
            "payload": {"profile": {"user_name": "Mansur"}}
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "upsert_sage_profile");
        assert_eq!(decision["state"]["class"], "sage_profile");
        assert_eq!(decision["reason"], "workspace_id_missing");
    }

    #[test]
    fn upsert_sage_profile_allows_owner_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "sage_profile_upsert",
            "workspace_id": "workspace-1",
            "actor_id": "owner-1",
            "payload": {"profile": {"user_name": "Mansur"}}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "upsert_sage_profile");
        assert_eq!(decision["state"]["class"], "sage_profile");
        assert_eq!(decision["next_action"], "upsert_sage_profile");
    }
}

#[cfg(test)]
mod runtime_state_store_capability_registry_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn save_mcp_server_registry_allows_owner_write_without_workspace() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "mcp_server_registry_save",
            "actor_id": "system",
            "payload": {"version": 1, "workspaces": {"workspace-1": {"servers": {}}}}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "save_mcp_server_registry");
        assert_eq!(decision["state"]["class"], "mcp_server_registry");
        assert_eq!(decision["scope"]["workspace_id"], "");
        assert_eq!(decision["next_action"], "save_mcp_server_registry");
    }

    #[test]
    fn save_installed_skill_registry_requires_payload() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "save_installed_skill_registry",
            "actor_id": "system"
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "save_installed_skill_registry");
        assert_eq!(decision["state"]["class"], "installed_skill_registry");
        assert_eq!(decision["reason"], "state_payload_missing");
    }

    #[test]
    fn save_installed_skill_registry_requires_owner_access() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "installed_skill_registry_save",
            "owner_access": false,
            "payload": {"version": 2, "items": {}}
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "save_installed_skill_registry");
        assert_eq!(decision["reason"], "owner_access_denied");
    }
}

#[cfg(test)]
mod runtime_state_store_secret_material_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn write_vault_key_file_requires_owner_access() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "write_vault_key_file",
            "owner_access": false,
            "payload": {"path": "/tmp/vault", "secret_length": 86}
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "write_vault_key_file");
        assert_eq!(decision["state"]["class"], "secret_material");
        assert_eq!(decision["reason"], "owner_access_denied");
    }

    #[test]
    fn write_jwt_secret_file_allows_owner_write_without_workspace() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "jwt_secret_file_write",
            "actor_id": "system",
            "payload": {"path": "/tmp/jwt", "secret_length": 64}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "write_jwt_secret_file");
        assert_eq!(decision["state"]["class"], "secret_material");
        assert_eq!(decision["scope"]["workspace_id"], "");
        assert_eq!(decision["next_action"], "write_jwt_secret_file");
    }
}

#[cfg(test)]
mod runtime_state_store_execution_artifact_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn persist_artifact_record_requires_workspace() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "persist_artifact_record",
            "payload": {"artifact_id": "artifact-1", "run_id": "run-1"}
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "persist_artifact_record");
        assert_eq!(decision["state"]["class"], "artifact_records");
        assert_eq!(decision["reason"], "workspace_id_missing");
    }

    #[test]
    fn hosted_sandbox_base_image_write_allows_global_owner_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "hosted_sandbox_base_image_write",
            "actor_id": "system",
            "payload": {"image_root": "/tmp/image", "read_only": true}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "write_hosted_sandbox_base_image");
        assert_eq!(decision["state"]["class"], "execution_sandbox_image");
        assert_eq!(decision["next_action"], "write_hosted_sandbox_base_image");
    }

    #[test]
    fn cli_companion_state_save_requires_payload() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "save_cli_companion_state",
            "actor_id": "system"
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "save_cli_companion_state");
        assert_eq!(decision["state"]["class"], "cli_companion_state");
        assert_eq!(decision["reason"], "state_payload_missing");
    }
}

#[cfg(test)]
mod runtime_state_store_worker_marketplace_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn write_hosted_worker_output_allows_global_owner_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "hosted_worker_output_write",
            "actor_id": "system",
            "payload": {"output_path": "/tmp/result.json", "status": "completed", "byte_size": 64}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "write_hosted_worker_output");
        assert_eq!(decision["state"]["class"], "hosted_worker_output");
        assert_eq!(decision["scope"]["workspace_id"], "");
        assert_eq!(decision["next_action"], "write_hosted_worker_output");
    }

    #[test]
    fn save_marketplace_distribution_state_requires_workspace() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "save_marketplace_distribution_state",
            "payload": {"packages": {}, "installs": {}}
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "save_marketplace_distribution_state");
        assert_eq!(decision["state"]["class"], "marketplace_distribution");
        assert_eq!(decision["reason"], "workspace_id_missing");
    }

    #[test]
    fn save_marketplace_distribution_state_allows_workspace_owner_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "marketplace_distribution_state_save",
            "workspace_id": "workspace-1",
            "actor_id": "system",
            "payload": {"packages": {}, "installs": {}}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "save_marketplace_distribution_state");
        assert_eq!(
            decision["next_action"],
            "save_marketplace_distribution_state"
        );
    }
}

#[cfg(test)]
mod runtime_state_store_sage_dreaming_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn sage_dreaming_memory_state_requires_workspace() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "write_sage_dreaming_memory_state",
            "payload": {"stage": "light_sleep", "entries": []}
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "write_sage_dreaming_memory_state");
        assert_eq!(decision["state"]["class"], "sage_dreaming");
        assert_eq!(decision["reason"], "workspace_id_missing");
    }

    #[test]
    fn sage_dreaming_staging_file_allows_workspace_owner_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "sage_dreaming_staging_file_write",
            "workspace_id": "workspace-1",
            "actor_id": "system",
            "payload": {"path": "/tmp/staging.json", "stage": "deep_sleep"}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "write_sage_dreaming_staging_file");
        assert_eq!(decision["state"]["class"], "sage_dreaming");
        assert_eq!(decision["next_action"], "write_sage_dreaming_staging_file");
    }
}

#[cfg(test)]
mod runtime_state_store_mini_apps_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn save_mini_apps_state_requires_workspace() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "save_mini_apps_state",
            "payload": {"apps": {}}
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "save_mini_apps_state");
        assert_eq!(decision["state"]["class"], "mini_apps");
        assert_eq!(decision["reason"], "workspace_id_missing");
    }

    #[test]
    fn save_mini_apps_state_allows_workspace_owner_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "mini_apps_state_save",
            "workspace_id": "workspace-1",
            "actor_id": "system",
            "payload": {"apps": {"flashcards": {"id": "flashcards"}}}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "save_mini_apps_state");
        assert_eq!(decision["state"]["class"], "mini_apps");
        assert_eq!(decision["next_action"], "save_mini_apps_state");
    }
}

#[cfg(test)]
mod runtime_state_store_workspace_context_profile_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn save_workspace_context_file_requires_workspace() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "save_workspace_context_file",
            "payload": {"filename": "USER.md", "content_bytes": 12}
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "save_workspace_context_file");
        assert_eq!(decision["state"]["class"], "workspace_context_files");
        assert_eq!(decision["reason"], "workspace_id_missing");
    }

    #[test]
    fn initialize_workspace_context_file_allows_workspace_owner_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "workspace_context_file_initialize",
            "workspace_id": "workspace-1",
            "actor_id": "system",
            "payload": {"filename": "SOUL.md", "content_bytes": 24}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "initialize_workspace_context_file");
        assert_eq!(decision["state"]["class"], "workspace_context_files");
        assert_eq!(decision["next_action"], "initialize_workspace_context_file");
    }

    #[test]
    fn save_agent_computer_profile_state_allows_workspace_owner_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "agent_computer_profile_state_save",
            "workspace_id": "workspace-1",
            "actor_id": "system",
            "payload": {"profiles": {"acp_1": {"environment_kind": "cloud_computer"}}}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "save_agent_computer_profile_state");
        assert_eq!(decision["state"]["class"], "agent_computer_profiles");
        assert_eq!(decision["next_action"], "save_agent_computer_profile_state");
    }
}

#[cfg(test)]
mod runtime_state_store_profile_api_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn profile_api_file_write_allows_global_owner_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "profile_api_file_write",
            "actor_id": "system",
            "payload": {"path": "/tmp/profile.json", "kind": "json"}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "write_profile_api_file");
        assert_eq!(decision["state"]["class"], "profile_api_files");
        assert_eq!(decision["scope"]["workspace_id"], "");
        assert_eq!(decision["next_action"], "write_profile_api_file");
    }

    #[test]
    fn profile_api_file_write_requires_payload() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "write_profile_api_file",
            "actor_id": "system"
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "write_profile_api_file");
        assert_eq!(decision["reason"], "state_payload_missing");
    }
}

#[cfg(test)]
mod runtime_state_store_shared_output_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn runtime_common_json_write_allows_global_owner_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "runtime_common_json_write",
            "actor_id": "system",
            "payload": {"path": "/tmp/runtime.json", "payload_bytes": 16}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "write_runtime_common_json");
        assert_eq!(decision["state"]["class"], "runtime_common_json_files");
        assert_eq!(decision["scope"]["workspace_id"], "");
        assert_eq!(decision["next_action"], "write_runtime_common_json");
    }

    #[test]
    fn acp_manager_json_write_allows_global_owner_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "acp_manager_json_write",
            "actor_id": "system",
            "payload": {"path": "/tmp/acp.json", "payload_bytes": 18}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "write_acp_manager_json");
        assert_eq!(decision["state"]["class"], "acp_manager_state");
        assert_eq!(decision["next_action"], "write_acp_manager_json");
    }

    #[test]
    fn session_diagnostics_file_write_allows_global_or_workspace_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "session_diagnostics_file_write",
            "workspace_id": "workspace-1",
            "actor_id": "system",
            "payload": {"output_path": "/tmp/diagnostics.json", "payload_bytes": 64}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "write_session_diagnostics_file");
        assert_eq!(decision["state"]["class"], "session_diagnostics_files");
        assert_eq!(decision["next_action"], "write_session_diagnostics_file");
    }

    #[test]
    fn agent_memory_daily_log_append_requires_workspace() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "append_agent_memory_daily_log",
            "actor_id": "system",
            "payload": {"log_path": "/tmp/memory.md", "entry_bytes": 24}
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "append_agent_memory_daily_log");
        assert_eq!(decision["state"]["class"], "agent_memory_daily_logs");
        assert_eq!(decision["reason"], "workspace_id_missing");
    }

    #[test]
    fn no_provider_summary_write_requires_payload() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "write_no_provider_summary",
            "actor_id": "system"
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "write_no_provider_summary");
        assert_eq!(decision["state"]["class"], "no_provider_summaries");
        assert_eq!(decision["reason"], "state_payload_missing");
    }

    #[test]
    fn public_bot_drill_report_write_allows_global_owner_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "public_bot_drill_report_write",
            "actor_id": "system",
            "payload": {"output_path": "/tmp/drill.md", "json_path": "/tmp/drill.json", "markdown_bytes": 8, "json_bytes": 12}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "write_public_bot_drill_report");
        assert_eq!(decision["state"]["class"], "public_bot_drill_reports");
        assert_eq!(decision["next_action"], "write_public_bot_drill_report");
    }

    #[test]
    fn telegram_media_file_write_allows_global_owner_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "telegram_media_file_write",
            "actor_id": "system",
            "payload": {"dest_path": "/tmp/media.jpg", "max_bytes": 1024}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "write_telegram_media_file");
        assert_eq!(decision["state"]["class"], "telegram_media_files");
        assert_eq!(decision["next_action"], "write_telegram_media_file");
    }

    #[test]
    fn machine_capability_probe_file_write_allows_global_owner_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "machine_capability_probe_file_write",
            "actor_id": "system",
            "payload": {"probe_file": "/tmp/write-test.tmp", "payload_bytes": 2}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "write_machine_capability_probe_file");
        assert_eq!(decision["state"]["class"], "machine_capability_probe");
        assert_eq!(
            decision["next_action"],
            "write_machine_capability_probe_file"
        );
    }

    #[test]
    fn dropbox_download_file_write_allows_global_owner_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "dropbox_download_file_write",
            "actor_id": "system",
            "payload": {"dropbox_path": "/remote/file.txt", "local_path": "/tmp/file.txt", "content_bytes": 12}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "write_dropbox_download_file");
        assert_eq!(decision["state"]["class"], "dropbox_download_files");
        assert_eq!(decision["next_action"], "write_dropbox_download_file");
    }

    #[test]
    fn generated_image_file_write_allows_global_owner_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "generated_image_file_write",
            "actor_id": "system",
            "payload": {"path": "/tmp/image.png", "payload_bytes": 24}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "write_generated_image_file");
        assert_eq!(decision["state"]["class"], "generated_image_files");
        assert_eq!(decision["next_action"], "write_generated_image_file");
    }

    #[test]
    fn telegram_poll_lock_file_write_allows_global_owner_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "telegram_poll_lock_file_write",
            "actor_id": "system",
            "payload": {"lock_path": "/tmp/getupdates.lock", "payload_bytes": 0}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "write_telegram_poll_lock_file");
        assert_eq!(decision["state"]["class"], "telegram_poll_lock_files");
        assert_eq!(decision["next_action"], "write_telegram_poll_lock_file");
    }

    #[test]
    fn skills_registry_file_write_allows_global_owner_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "skills_registry_file_write",
            "actor_id": "system",
            "payload": {"path": "/tmp/skill.json", "kind": "json", "payload_bytes": 40}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "write_skills_registry_file");
        assert_eq!(decision["state"]["class"], "skills_registry_files");
        assert_eq!(decision["next_action"], "write_skills_registry_file");
    }

    #[test]
    fn deployed_agent_knowledge_file_write_requires_workspace() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "deployed_agent_knowledge_file_write",
            "actor_id": "system",
            "payload": {"deployed_agent_id": "agent-1", "destination": "/tmp/knowledge.md", "content_bytes": 42}
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "write_deployed_agent_knowledge_file");
        assert_eq!(decision["state"]["class"], "deployed_agent_knowledge_files");
        assert_eq!(decision["reason"], "workspace_id_missing");
    }

    #[test]
    fn outcome_pack_spreadsheet_file_write_allows_global_owner_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "outcome_pack_spreadsheet_file_write",
            "actor_id": "system",
            "payload": {"pack_id": "spreadsheet_ops", "file_path": "/tmp/outcome.csv", "payload_bytes": 128}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "write_outcome_pack_spreadsheet_file");
        assert_eq!(decision["state"]["class"], "outcome_pack_spreadsheet_files");
        assert_eq!(
            decision["next_action"],
            "write_outcome_pack_spreadsheet_file"
        );
    }

    #[test]
    fn outcome_pack_document_file_write_allows_global_owner_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "outcome_pack_document_file_write",
            "actor_id": "system",
            "payload": {"pack_id": "document_studio", "file_path": "/tmp/outcome.docx", "payload_bytes": 4096}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "write_outcome_pack_document_file");
        assert_eq!(decision["state"]["class"], "outcome_pack_document_files");
        assert_eq!(decision["next_action"], "write_outcome_pack_document_file");
    }

    #[test]
    fn outcome_pack_remote_sync_file_write_requires_payload() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "write_outcome_pack_remote_sync_file",
            "actor_id": "system"
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "write_outcome_pack_remote_sync_file");
        assert_eq!(decision["state"]["class"], "outcome_pack_remote_sync_files");
        assert_eq!(decision["reason"], "state_payload_missing");
    }

    #[test]
    fn artifact_content_file_write_requires_workspace() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "artifact_content_file_write",
            "actor_id": "system",
            "run_id": "run-1",
            "payload": {"artifact_id": "artifact-1", "target": "/tmp/artifact.bin", "content_bytes": 256}
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "write_artifact_content_file");
        assert_eq!(decision["state"]["class"], "artifact_content_files");
        assert_eq!(decision["reason"], "workspace_id_missing");
    }

    #[test]
    fn external_write_execute_requires_workspace() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "external_write_execute",
            "actor_id": "system",
            "run_id": "run-1",
            "payload": {"category": "email_send", "target_system": "gmail", "payload_hash": "hash"}
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "execute_external_write_once");
        assert_eq!(decision["state"]["class"], "external_write_executions");
        assert_eq!(decision["reason"], "workspace_id_missing");
    }

    #[test]
    fn external_write_execute_allows_workspace_owner_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "execute_external_write_once",
            "workspace_id": "workspace-1",
            "actor_id": "system",
            "run_id": "run-1",
            "payload": {"category": "email_send", "target_system": "gmail", "payload_hash": "hash"}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "execute_external_write_once");
        assert_eq!(decision["state"]["class"], "external_write_executions");
        assert_eq!(decision["next_action"], "execute_external_write_once");
    }

    #[test]
    fn gateway_tool_approval_create_requires_workspace() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "gateway_tool_approval_create",
            "actor_id": "system",
            "run_id": "run-1",
            "payload": {"gateway_id": "gateway-1", "capability_id": "browser.click"}
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "create_gateway_tool_approval");
        assert_eq!(decision["state"]["class"], "gateway_approvals");
        assert_eq!(decision["reason"], "workspace_id_missing");
    }

    #[test]
    fn gateway_tool_approval_resolve_allows_workspace_owner_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "gateway_tool_approval_resolve",
            "workspace_id": "workspace-1",
            "actor_id": "system",
            "run_id": "run-1",
            "payload": {"approval_id": "approval-1", "decision": "approved"}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "resolve_gateway_tool_approval");
        assert_eq!(decision["state"]["class"], "gateway_approvals");
        assert_eq!(decision["next_action"], "resolve_gateway_tool_approval");
    }

    #[test]
    fn gateway_tool_approval_execute_allows_workspace_owner_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "gateway_tool_approval_execute",
            "workspace_id": "workspace-1",
            "actor_id": "system",
            "run_id": "run-1",
            "payload": {"approval_id": "approval-1", "execution_status": "completed"}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "execute_gateway_tool_approval");
        assert_eq!(decision["state"]["class"], "gateway_approvals");
        assert_eq!(decision["next_action"], "execute_gateway_tool_approval");
    }

    #[test]
    fn gateway_protocol_request_frame_send_requires_workspace() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "gateway_protocol_request_frame_send",
            "actor_id": "system",
            "payload": {"gateway_id": "gateway-1", "message_type": "tool.invoke", "frame_id": "request-1"}
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "send_gateway_protocol_request_frame");
        assert_eq!(decision["state"]["class"], "gateway_protocol_frames");
        assert_eq!(decision["reason"], "workspace_id_missing");
    }

    #[test]
    fn gateway_protocol_request_frame_send_allows_workspace_owner_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "send_gateway_protocol_request_frame",
            "workspace_id": "workspace-1",
            "actor_id": "system",
            "run_id": "run-1",
            "payload": {"gateway_id": "gateway-1", "message_type": "tool.invoke", "frame_id": "request-1"}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "send_gateway_protocol_request_frame");
        assert_eq!(decision["state"]["class"], "gateway_protocol_frames");
        assert_eq!(
            decision["next_action"],
            "send_gateway_protocol_request_frame"
        );
    }

    #[test]
    fn channel_execution_lease_acquire_requires_workspace() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "channel_execution_lease_acquire",
            "actor_id": "system",
            "payload": {"lease_id": "lease-1", "thread_id": "thread-1"}
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "acquire_channel_execution_lease");
        assert_eq!(decision["state"]["class"], "channel_execution_leases");
        assert_eq!(decision["reason"], "workspace_id_missing");
    }

    #[test]
    fn channel_execution_lease_release_allows_workspace_owner_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "channel_execution_lease_release",
            "workspace_id": "workspace-1",
            "actor_id": "system",
            "payload": {"lease_id": "lease-1"}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "release_channel_execution_lease");
        assert_eq!(decision["state"]["class"], "channel_execution_leases");
        assert_eq!(decision["next_action"], "release_channel_execution_lease");
    }

    #[test]
    fn direct_tool_execution_start_requires_workspace() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "direct_tool_execution_start",
            "actor_id": "system",
            "run_id": "run-1",
            "payload": {"tool_call_id": "tool-call-1", "tool_name": "shell.exec"}
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["operation"], "start_direct_tool_execution");
        assert_eq!(decision["state"]["class"], "direct_tool_execution");
        assert_eq!(decision["reason"], "workspace_id_missing");
    }

    #[test]
    fn direct_tool_execution_complete_allows_workspace_owner_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "direct_tool_execution_complete",
            "workspace_id": "workspace-1",
            "actor_id": "system",
            "run_id": "run-1",
            "payload": {"tool_call_id": "tool-call-1", "tool_name": "shell.exec", "status": "completed"}
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "complete_direct_tool_execution");
        assert_eq!(decision["state"]["class"], "direct_tool_execution");
        assert_eq!(decision["next_action"], "complete_direct_tool_execution");
    }
}

#[cfg(test)]
mod run_approval_repository_state_store_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn approval_request_create_uses_run_approval_state_class() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "create_or_update_approval_request",
            "run_id": "run-1",
            "workspace_id": "workspace-1",
            "tenant_id": "tenant-1",
            "status": "requested",
            "payload": true,
            "payload_bytes": 64,
            "durable_pool_available": true
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "create_or_update_approval_request");
        assert_eq!(decision["state"]["class"], "run_approvals");
        assert_eq!(decision["next_action"], "write_run_approval_request");
    }

    #[test]
    fn approval_resolution_requires_run_id() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "resolve_approval_if_pending",
            "workspace_id": "workspace-1",
            "tenant_id": "tenant-1",
            "status": "approved",
            "payload": true,
            "payload_bytes": 64,
            "durable_pool_available": true
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "run_id_missing");
        assert_eq!(decision["state"]["class"], "run_approvals");
    }

    #[test]
    fn approval_resolution_record_allows_payload_write() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "record_approval_resolution",
            "run_id": "run-1",
            "workspace_id": "workspace-1",
            "tenant_id": "tenant-1",
            "status": "rejected",
            "payload": true,
            "payload_bytes": 64,
            "durable_pool_available": true
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "record_approval_resolution");
        assert_eq!(decision["next_action"], "record_run_approval_resolution");
    }

    #[test]
    fn fleet_queue_partition_upsert_uses_partition_state_class() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "upsert_fleet_queue_partition",
            "workspace_id": "workspace-1",
            "tenant_id": "tenant-1",
            "status": "strained",
            "payload": {"summary": "Partition backlog elevated"},
            "payload_bytes": 64,
            "durable_pool_available": true
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "upsert_fleet_queue_partition");
        assert_eq!(decision["state"]["class"], "fleet_queue_partitions");
        assert_eq!(decision["next_action"], "write_fleet_queue_partition");
    }

    #[test]
    fn delete_live_run_is_destructive_but_allows_terminal_status() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "delete_live_run",
            "run_id": "run-1",
            "workspace_id": "workspace-1",
            "tenant_id": "tenant-1",
            "status": "failed",
            "durable_pool_available": true
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "delete_live_run");
        assert_eq!(decision["state"]["class"], "live_runs");
        assert_eq!(decision["next_action"], "delete_live_run_state");
    }
}

#[cfg(test)]
mod vault_blob_backup_state_store_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn write_vault_blob_backup_allows_owner_with_payload() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "write_vault_blob_backup",
            "state_class": "secret_material",
            "actor_id": "system",
            "status": "active",
            "payload": {"vault_blob_base64": "dGVzdC1ibG9i", "environment": "dev", "backup_version": "vault_blob_v1"},
            "payload_bytes": 32,
            "workspace_access": true,
            "owner_access": true,
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "write_vault_blob_backup");
        assert_eq!(decision["state"]["class"], "secret_material");
        assert_eq!(decision["next_action"], "write_vault_blob_backup");
    }

    #[test]
    fn write_vault_blob_backup_blocks_without_owner_access() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "write_vault_blob_backup",
            "state_class": "secret_material",
            "actor_id": "member-1",
            "status": "active",
            "payload": {"vault_blob_base64": "dGVzdC1ibG9i"},
            "payload_bytes": 12,
            "workspace_access": true,
            "owner_access": false,
        }));

        assert_eq!(decision["decision"], "block");
        assert!(decision["reason"].as_str().unwrap_or("").contains("owner_access_denied"));
    }

    #[test]
    fn write_vault_blob_backup_blocks_without_payload() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "write_vault_blob_backup",
            "state_class": "secret_material",
            "actor_id": "system",
            "status": "active",
            "workspace_access": true,
            "owner_access": true,
        }));

        assert_eq!(decision["decision"], "block");
        assert!(decision["reason"].as_str().unwrap_or("").contains("payload_missing"));
    }

    #[test]
    fn read_vault_blob_backup_allows_owner_without_payload() {
        // read operations don't need a payload — they fetch from cloud
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "read_vault_blob_backup",
            "state_class": "secret_material",
            "actor_id": "system",
            "status": "active",
            "workspace_access": true,
            "owner_access": true,
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "read_vault_blob_backup");
        assert_eq!(decision["state"]["class"], "secret_material");
        assert_eq!(decision["next_action"], "read_vault_blob_backup");
    }

    #[test]
    fn read_vault_blob_backup_blocks_without_owner_access() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "read_vault_blob_backup",
            "state_class": "secret_material",
            "actor_id": "member-1",
            "status": "active",
            "workspace_access": true,
            "owner_access": false,
        }));

        assert_eq!(decision["decision"], "block");
        assert!(decision["reason"].as_str().unwrap_or("").contains("owner_access_denied"));
    }

    #[test]
    fn write_channel_pairings_backup_allows_owner_with_payload() {
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "write_channel_pairings_backup",
            "state_class": "secret_material",
            "actor_id": "system",
            "status": "active",
            "payload": {"channel": "telegram", "pairings_blob_base64": "dGVzdA==", "backup_version": "channel_pairings_v1"},
            "payload_bytes": 64,
            "workspace_access": true,
            "owner_access": true,
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "write_channel_pairings_backup");
        assert_eq!(decision["state"]["class"], "secret_material");
        assert_eq!(decision["next_action"], "write_channel_pairings_backup");
    }

    #[test]
    fn vault_blob_backup_does_not_require_workspace() {
        // Vault operations are per-box, not per-workspace
        let decision = runtime_state_store_decision_command(&json!({
            "operation": "write_vault_blob_backup",
            "state_class": "secret_material",
            "actor_id": "system",
            "status": "active",
            "payload": {"vault_blob_base64": "dGVzdA=="},
            "payload_bytes": 8,
            "owner_access": true,
        }));

        assert_eq!(decision["decision"], "allow");
    }
}

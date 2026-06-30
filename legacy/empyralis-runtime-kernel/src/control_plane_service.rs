use serde_json::{json, Value};

const SERVICE_OPERATIONS: &[&str] = &[
    "tenant_create",
    "tenant_update",
    "workspace_create",
    "workspace_update",
    "workspace_ai_route_update",
    "transparency_settings_update",
    "workspace_routing_update",
    "workspace_policy_update",
    "sage_tool_policy_update",
    "provider_models_refresh",
    "workspace_archive",
    "workspace_delete",
    "membership_add",
    "membership_update",
    "membership_remove",
    "invite_create",
    "invite_accept",
    "invite_revoke",
    "pilot_invite_create",
    "pilot_invite_claim",
    "pilot_invite_revoke",
    "workspace_tenant_binding_ensure",
    "user_profile_update",
    "billing_update",
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
    "entitlement_check",
    "quota_update",
    "thread_create",
    "thread_update",
    "thread_archive",
    "thread_delete",
    "run_record_create",
    "run_record_update",
    "run_record_read",
    "run_record_list",
    "deployed_agent_record_write",
    "gateway_record_write",
    "session_record_write",
    "webhook_ingest",
    "audit_export",
    "admin_impersonation",
    "workspace_emergency_stop",
    "public_route_update",
    "external_user_privacy_request_write",
    "external_user_privacy_audit_write",
    "external_user_privacy_delete",
    "deployed_agent_scope_data_delete",
    "workspace_scope_data_delete",
    "deployed_agent_daily_message_quota_consume",
    "deployed_agent_daily_message_warning_update",
    "deployed_agent_cost_ledger_write",
    "workspace_hosted_ai_cost_ledger_write",
    "credit_ledger_event_write",
    "workspace_ai_route_update",
    "transparency_settings_update",
    "workspace_routing_update",
    "workspace_policy_update",
    "sage_tool_policy_update",
    "provider_models_refresh",
    "secret_reference_write",
];

const DESTRUCTIVE_OPERATIONS: &[&str] = &[
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
];

const OWNER_REQUIRED_OPERATIONS: &[&str] = &[
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
];

const ADMIN_REQUIRED_OPERATIONS: &[&str] = &[
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
];

const EXTERNAL_WRITE_OPERATIONS: &[&str] = &[
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
];

const BILLING_GATED_OPERATIONS: &[&str] = &[
    "workspace_create",
    "deployed_agent_record_write",
    "gateway_record_write",
    "session_record_write",
    "public_route_update",
    "workspace_ai_route_update",
    "workspace_billing_defaults_write",
    "workspace_billing_plan_update",
    "workspace_billing_account_write",
    "workspace_billing_subscription_write",
];

pub fn control_plane_service_decision_command(payload: &Value) -> Result<Value, String> {
    let operation = text(payload, "operation", "unknown");
    let record_type = text(payload, "record_type", "service");
    let actor_role = text(payload, "actor_role", "member");
    let tenant_id = payload
        .get("tenant_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let workspace_id = payload
        .get("workspace_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let actor_id = payload
        .get("actor_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let target_actor_id = payload
        .get("target_actor_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let run_id = payload.get("run_id").and_then(Value::as_str).unwrap_or("");
    let thread_id = payload
        .get("thread_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let agent_id = payload
        .get("agent_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let gateway_id = payload
        .get("gateway_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let session_id = payload
        .get("session_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let webhook_id = payload
        .get("webhook_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let idempotency_key = payload
        .get("idempotency_key")
        .and_then(Value::as_str)
        .unwrap_or("");
    let target_status = text(payload, "target_status", "active");
    let source = text(payload, "source", "internal");
    let plan_tier = text(payload, "plan_tier", "free");

    let approval_provided = flag(payload, "approval_provided", false);
    let owner_approval_provided = flag(payload, "owner_approval_provided", approval_provided);
    let workspace_access = flag(payload, "workspace_access", true);
    let owner_access = flag(
        payload,
        "owner_access",
        matches!(actor_role.as_str(), "owner" | "system" | "service"),
    );
    let admin_access = flag(
        payload,
        "admin_access",
        matches!(
            actor_role.as_str(),
            "owner" | "admin" | "system" | "service"
        ),
    );
    let billing_entitled = flag(payload, "billing_entitled", true);
    let quota_ok = flag(payload, "quota_ok", true);
    let kill_switch_enabled = flag(payload, "kill_switch_enabled", false);
    let safe_mode_enabled = flag(payload, "safe_mode_enabled", false);
    let retention_lock = flag(payload, "retention_lock", false);
    let terminal_record = flag(payload, "terminal_record", false);
    let status_transition_valid = flag(payload, "status_transition_valid", true);
    let webhook_signature_valid = flag(payload, "webhook_signature_valid", true);
    let privacy_export_approval = flag(payload, "privacy_export_approval", false);
    let public_route_enabled = flag(payload, "public_route_enabled", false);
    let dry_run = flag(payload, "dry_run", false);

    let known_operation = contains(SERVICE_OPERATIONS, &operation);
    let destructive_operation = contains(DESTRUCTIVE_OPERATIONS, &operation);
    let owner_required = contains(OWNER_REQUIRED_OPERATIONS, &operation);
    let admin_required = contains(ADMIN_REQUIRED_OPERATIONS, &operation);
    let external_write = contains(EXTERNAL_WRITE_OPERATIONS, &operation) || source == "webhook";
    let billing_gated = contains(BILLING_GATED_OPERATIONS, &operation);
    let requires_workspace = !matches!(
        operation.as_str(),
        "tenant_create" | "tenant_update" | "entitlement_check" | "webhook_ingest"
    );
    let scope_type = text(payload, "scope_type", "workspace");
    let requires_workspace = requires_workspace
        && !(matches!(
            operation.as_str(),
            "governance_hold_write" | "governance_hold_release"
        ) && scope_type == "tenant");
    let requires_run = operation == "run_record_create" || operation == "run_record_update";
    let requires_thread = operation == "thread_update"
        || operation == "thread_archive"
        || operation == "thread_delete"
        || operation == "agent_turn_transcript_event_append"
        || operation == "agent_turn_upsert";
    let requires_agent =
        operation == "deployed_agent_record_write" || operation == "public_route_update";
    let requires_agent = requires_agent
        || operation == "external_user_privacy_request_write"
        || operation == "external_user_privacy_audit_write"
        || operation == "external_user_privacy_delete"
        || operation == "deployed_agent_scope_data_delete"
        || operation == "deployed_agent_daily_message_quota_consume"
        || operation == "deployed_agent_daily_message_warning_update"
        || operation == "deployed_agent_cost_ledger_write";
    let requires_agent = requires_agent
        || operation == "channel_user_acquisition_touch_write"
        || operation == "channel_user_acquisition_conversion_write"
        || operation == "deployed_agent_upgrade_click_write";
    let requires_gateway = operation == "gateway_record_write";
    let requires_session = operation == "session_record_write"
        || operation == "agent_session_upsert"
        || operation == "agent_session_terminate";

    let mut block_reasons: Vec<String> = Vec::new();
    let mut approval_reasons: Vec<String> = Vec::new();

    if !known_operation {
        block_reasons.push("unknown_control_plane_service_operation".to_string());
    }
    if tenant_id.trim().is_empty() {
        block_reasons.push("missing_tenant".to_string());
    }
    if requires_workspace && workspace_id.trim().is_empty() {
        block_reasons.push("missing_workspace".to_string());
    }
    if actor_id.trim().is_empty() {
        block_reasons.push("missing_actor".to_string());
    }
    if requires_workspace && !workspace_access {
        block_reasons.push("workspace_access_denied".to_string());
    }
    if owner_required && !owner_access {
        block_reasons.push("owner_access_required".to_string());
    }
    if admin_required && !admin_access {
        block_reasons.push("admin_access_required".to_string());
    }
    if billing_gated && !billing_entitled {
        block_reasons.push("billing_entitlement_missing".to_string());
    }
    if !quota_ok && operation != "entitlement_check" {
        block_reasons.push("quota_exceeded".to_string());
    }
    if kill_switch_enabled && operation != "workspace_emergency_stop" {
        block_reasons.push("control_plane_kill_switch_enabled".to_string());
    }
    if destructive_operation && retention_lock {
        block_reasons.push("retention_lock_active".to_string());
    }
    if terminal_record && !can_touch_terminal_record(&operation) {
        block_reasons.push("terminal_record_locked".to_string());
    }
    if !status_transition_valid {
        block_reasons.push("invalid_status_transition".to_string());
    }
    if operation == "webhook_ingest" && !webhook_signature_valid {
        block_reasons.push("invalid_webhook_signature".to_string());
    }
    if external_write && idempotency_key.trim().is_empty() {
        block_reasons.push("missing_idempotency_key".to_string());
    }
    if operation == "membership_remove" && target_actor_id == actor_id {
        block_reasons.push("cannot_remove_self_membership".to_string());
    }
    if operation == "invite_revoke" && target_status == "accepted" {
        block_reasons.push("invite_already_accepted".to_string());
    }
    if operation == "run_record_update" && run_id.trim().is_empty() {
        block_reasons.push("missing_run_id".to_string());
    }
    if requires_run && run_id.trim().is_empty() && operation != "run_record_create" {
        block_reasons.push("missing_run_id".to_string());
    }
    if requires_thread && thread_id.trim().is_empty() {
        block_reasons.push("missing_thread_id".to_string());
    }
    if requires_agent && agent_id.trim().is_empty() {
        block_reasons.push("missing_agent_id".to_string());
    }
    if requires_gateway && gateway_id.trim().is_empty() {
        block_reasons.push("missing_gateway_id".to_string());
    }
    if requires_session && session_id.trim().is_empty() {
        block_reasons.push("missing_session_id".to_string());
    }
    if operation == "webhook_ingest" && webhook_id.trim().is_empty() {
        block_reasons.push("missing_webhook_id".to_string());
    }
    if operation == "audit_export" && !privacy_export_approval {
        block_reasons.push("privacy_export_not_approved".to_string());
    }
    if operation == "public_route_update" && !public_route_enabled {
        block_reasons.push("public_route_not_enabled".to_string());
    }

    let existing_record_idempotent_return =
        operation == "invite_revoke" && target_status == "revoked";

    if destructive_operation && !existing_record_idempotent_return {
        approval_reasons.push("destructive_control_plane_operation".to_string());
    }
    if safe_mode_enabled && (admin_required || owner_required || external_write) {
        approval_reasons.push("safe_mode_control_plane_write".to_string());
    }
    if operation == "secret_reference_write" {
        approval_reasons.push("secret_reference_write_requires_approval".to_string());
    }
    if operation == "admin_impersonation" {
        approval_reasons.push("admin_impersonation_requires_break_glass".to_string());
    }
    if operation == "workspace_emergency_stop" {
        approval_reasons.push("emergency_stop_requires_owner_audit".to_string());
    }

    let approval_required = !approval_provided && !approval_reasons.is_empty();
    let blocked = !block_reasons.is_empty();
    let decision = if blocked {
        "block"
    } else if approval_required {
        "requires_approval"
    } else {
        "allow"
    };
    let next_action = if blocked {
        "do_not_apply_control_plane_operation"
    } else if approval_required {
        "request_control_plane_approval"
    } else if dry_run {
        "return_control_plane_dry_run"
    } else if existing_record_idempotent_return {
        "return_existing_control_plane_record"
    } else if operation == "webhook_ingest" {
        "persist_webhook_event"
    } else if operation == "entitlement_check" {
        "return_entitlement_snapshot"
    } else if operation == "audit_export" {
        "create_audit_export_job"
    } else if destructive_operation {
        "apply_control_plane_destructive_write"
    } else if external_write || admin_required || owner_required {
        "apply_control_plane_write"
    } else {
        "allow_control_plane_read"
    };
    let audit_visibility = if operation == "admin_impersonation"
        || operation == "secret_reference_write"
        || operation == "audit_export"
    {
        "security_audit"
    } else if destructive_operation || owner_required || admin_required || approval_required {
        "admin_audit"
    } else {
        "standard_audit"
    };
    let reason = if blocked {
        block_reasons.join(",")
    } else if approval_required {
        approval_reasons.join(",")
    } else {
        "control_plane_service_operation_allowed".to_string()
    };
    let cacheable = !blocked
        && !approval_required
        && matches!(
            operation.as_str(),
            "run_record_read" | "run_record_list" | "entitlement_check"
        );
    let mutating_operation = !existing_record_idempotent_return
        && !matches!(
            operation.as_str(),
            "run_record_read" | "run_record_list" | "entitlement_check"
        );

    Ok(json!({
        "ok": !blocked,
        "decision": decision,
        "reason": reason,
        "operation": operation,
        "record_type": record_type,
        "next_action": next_action,
        "approval_required": approval_required,
        "cacheable": cacheable,
        "audit_visibility": audit_visibility,
        "mutation_plan": {
            "operation": operation,
            "record_type": record_type,
            "apply": !blocked && !approval_required && !dry_run && mutating_operation,
            "next_action": next_action,
            "idempotency_key": idempotency_key,
            "target_status": target_status,
            "mutating": mutating_operation,
            "destructive": destructive_operation,
            "external_write": external_write,
            "billing_gated": billing_gated,
        },
        "service_scope": {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "actor_id": actor_id,
            "target_actor_id": target_actor_id,
            "run_id": run_id,
            "thread_id": thread_id,
            "agent_id": agent_id,
            "gateway_id": gateway_id,
            "session_id": session_id,
            "webhook_id": webhook_id,
        },
        "governance": {
            "actor_role": actor_role,
            "workspace_access": workspace_access,
            "owner_access": owner_access,
            "admin_access": admin_access,
            "retention_lock": retention_lock,
            "terminal_record": terminal_record,
            "status_transition_valid": status_transition_valid,
            "safe_mode_enabled": safe_mode_enabled,
            "kill_switch_enabled": kill_switch_enabled,
        },
        "billing": {
            "plan_tier": plan_tier,
            "billing_gated": billing_gated,
            "billing_entitled": billing_entitled,
            "quota_ok": quota_ok,
        },
        "external_write": {
            "enabled": external_write,
            "source": source,
            "idempotency_key_present": !idempotency_key.trim().is_empty(),
            "webhook_signature_valid": webhook_signature_valid,
        },
        "approval": {
            "provided": approval_provided,
            "owner_approval_provided": owner_approval_provided,
            "reasons": approval_reasons,
        },
        "block_reasons": block_reasons,
        "normalized_flags": {
            "known_operation": known_operation,
            "destructive_operation": destructive_operation,
            "owner_required": owner_required,
            "admin_required": admin_required,
            "requires_workspace": requires_workspace,
            "dry_run": dry_run,
            "target_status": target_status,
        }
    }))
}

fn contains(values: &[&str], needle: &str) -> bool {
    values.iter().any(|value| *value == needle)
}

fn can_touch_terminal_record(operation: &str) -> bool {
    matches!(
        operation,
        "run_record_read" | "run_record_list" | "audit_export" | "entitlement_check"
    )
}

fn text(payload: &Value, key: &str, default: &str) -> String {
    payload
        .get(key)
        .and_then(Value::as_str)
        .map(normalize)
        .unwrap_or_else(|| normalize(default))
}

fn flag(payload: &Value, key: &str, default: bool) -> bool {
    match payload.get(key) {
        Some(Value::Bool(value)) => *value,
        Some(Value::String(value)) => match normalize(value).as_str() {
            "1" | "true" | "yes" | "y" | "enabled" | "allow" | "allowed" | "ok" => true,
            "0" | "false" | "no" | "n" | "disabled" | "deny" | "denied" | "block" => false,
            _ => default,
        },
        Some(Value::Number(value)) => value.as_i64().unwrap_or(0) != 0,
        _ => default,
    }
}

fn normalize(value: &str) -> String {
    value
        .trim()
        .to_ascii_lowercase()
        .replace('-', "_")
        .replace(' ', "_")
}

#[cfg(test)]
mod control_plane_service_workspace_admin_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn membership_remove_blocks_self_removal() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "membership_remove",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "user-1",
            "target_actor_id": "user-1",
            "actor_role": "owner",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "approval_provided": true,
            "owner_approval_provided": true
        }))
        .unwrap();

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "cannot_remove_self_membership");
    }

    #[test]
    fn secret_reference_write_requires_approval() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "secret_reference_write",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "owner-1",
            "actor_role": "owner",
            "idempotency_key": "provider_credential:ws-1:anthropic",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "approval_provided": false,
            "owner_approval_provided": false
        }))
        .unwrap();

        assert_eq!(decision["decision"], "requires_approval");
        assert_eq!(decision["approval_required"], true);
        assert_eq!(
            decision["reason"],
            "secret_reference_write_requires_approval"
        );
    }
}

#[cfg(test)]
mod control_plane_service_workspace_ai_route_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn workspace_ai_route_update_blocks_missing_billing_entitlement() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "workspace_ai_route_update",
            "record_type": "workspace_ai_route",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "owner-1",
            "actor_role": "owner",
            "idempotency_key": "workspace_ai_route:ws-1:user_api_key:openai:pro",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": false,
            "quota_ok": true,
            "approval_provided": true,
            "owner_approval_provided": true
        }))
        .unwrap();

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "billing_entitlement_missing");
    }

    #[test]
    fn workspace_ai_route_update_allows_owner_approved_route_mutation() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "workspace_ai_route_update",
            "record_type": "workspace_ai_route",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "owner-1",
            "actor_role": "owner",
            "idempotency_key": "workspace_ai_route:ws-1:user_api_key:openai:pro",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true,
            "approval_provided": true,
            "owner_approval_provided": true
        }))
        .unwrap();

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "workspace_ai_route_update");
        assert_eq!(decision["record_type"], "workspace_ai_route");
    }
}

#[cfg(test)]
mod control_plane_service_workspace_route_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn workspace_create_blocks_missing_billing_entitlement() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "workspace_create",
            "record_type": "workspace",
            "tenant_id": "tenant-1",
            "workspace_id": "pending:owner-1",
            "actor_id": "owner-1",
            "actor_role": "owner",
            "idempotency_key": "workspace_create:owner-1:Acme",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": false,
            "quota_ok": true,
            "approval_provided": true,
            "owner_approval_provided": true
        }))
        .unwrap();

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "billing_entitlement_missing");
    }

    #[test]
    fn workspace_update_blocks_missing_workspace_access() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "workspace_update",
            "record_type": "workspace",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "owner-1",
            "actor_role": "owner",
            "idempotency_key": "workspace_update:ws-1",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": false,
            "billing_entitled": true,
            "quota_ok": true,
            "approval_provided": true,
            "owner_approval_provided": true
        }))
        .unwrap();

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "workspace_access_denied");
    }
}

#[cfg(test)]
mod control_plane_service_transparency_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn transparency_settings_update_requires_owner_access() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "transparency_settings_update",
            "record_type": "workspace_transparency_settings",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "member-1",
            "actor_role": "member",
            "idempotency_key": "transparency_settings_update:ws-1",
            "owner_access": false,
            "admin_access": false,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true,
            "approval_provided": true,
            "owner_approval_provided": true
        }))
        .unwrap();

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "owner_access_required");
    }
}

#[cfg(test)]
mod control_plane_service_workspace_admin_route_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn workspace_policy_update_requires_owner_access() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "workspace_policy_update",
            "record_type": "workspace_policy",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "member-1",
            "actor_role": "member",
            "idempotency_key": "workspace_policy_update:ws-1",
            "owner_access": false,
            "admin_access": false,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true,
            "approval_provided": true,
            "owner_approval_provided": true
        }))
        .unwrap();

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "owner_access_required");
    }

    #[test]
    fn provider_models_refresh_blocks_quota_exceeded() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "provider_models_refresh",
            "record_type": "provider_models",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "owner-1",
            "actor_role": "owner",
            "idempotency_key": "provider_models_refresh:ws-1:anthropic",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": false,
            "approval_provided": true,
            "owner_approval_provided": true
        }))
        .unwrap();

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "quota_exceeded");
    }

    #[test]
    fn workspace_routing_update_allows_owner_write() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "workspace_routing_update",
            "record_type": "workspace_routing",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "owner-1",
            "actor_role": "owner",
            "idempotency_key": "workspace_routing_update:ws-1",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true,
            "approval_provided": true,
            "owner_approval_provided": true
        }))
        .unwrap();

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["operation"], "workspace_routing_update");
    }
}

#[cfg(test)]
mod control_plane_service_mutation_plan_boundary_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn allowed_write_returns_canonical_mutation_plan() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "workspace_routing_update",
            "record_type": "workspace_routing",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "owner-1",
            "actor_role": "owner",
            "target_status": "active",
            "idempotency_key": "workspace_routing_update:ws-1",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true,
            "approval_provided": true,
            "owner_approval_provided": true
        }))
        .unwrap();

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["cacheable"], false);
        assert_eq!(decision["mutation_plan"]["apply"], true);
        assert_eq!(
            decision["mutation_plan"]["operation"],
            "workspace_routing_update"
        );
        assert_eq!(
            decision["mutation_plan"]["record_type"],
            "workspace_routing"
        );
        assert_eq!(
            decision["mutation_plan"]["idempotency_key"],
            "workspace_routing_update:ws-1"
        );
    }

    #[test]
    fn cacheable_read_returns_non_mutating_plan() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "entitlement_check",
            "record_type": "entitlement",
            "tenant_id": "tenant-1",
            "workspace_id": "",
            "actor_id": "owner-1",
            "actor_role": "owner",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true,
            "approval_provided": true,
            "owner_approval_provided": true
        }))
        .unwrap();

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["cacheable"], true);
        assert_eq!(decision["mutation_plan"]["apply"], false);
        assert_eq!(decision["mutation_plan"]["mutating"], false);
        assert_eq!(
            decision["mutation_plan"]["next_action"],
            "return_entitlement_snapshot"
        );
    }
}

#[cfg(test)]
mod privacy_control_plane_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn external_user_privacy_request_write_allows_scoped_idempotent_request() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "external_user_privacy_request_write",
            "record_type": "external_user_privacy_request",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "external-user-1",
            "actor_role": "service",
            "agent_id": "dagent-1",
            "idempotency_key": "dagent-1:telegram:external-user-1:requested",
            "workspace_access": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("external_user_privacy_request_write")
        );
        assert_eq!(
            decision["record_type"].as_str(),
            Some("external_user_privacy_request")
        );
    }

    #[test]
    fn external_user_privacy_delete_blocks_missing_deployed_agent_scope() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "external_user_privacy_delete",
            "record_type": "external_user_privacy_delete",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "system",
            "actor_role": "system",
            "idempotency_key": "privacy-delete-1",
            "workspace_access": true,
            "admin_access": true,
            "owner_access": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("block"));
        assert!(decision["reason"]
            .as_str()
            .unwrap_or("")
            .contains("missing_agent_id"));
    }

    #[test]
    fn workspace_scope_data_delete_is_destructive_and_allowed_for_system() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "workspace_scope_data_delete",
            "record_type": "workspace_scope_data",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "system",
            "actor_role": "system",
            "idempotency_key": "workspace-1:scope_delete",
            "workspace_access": true,
            "admin_access": true,
            "owner_access": true,
            "approval_provided": true,
            "owner_approval_provided": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("workspace_scope_data_delete")
        );
        assert_eq!(
            decision["mutation_plan"]["destructive"].as_bool(),
            Some(true)
        );
    }
}

#[cfg(test)]
mod billing_usage_control_plane_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn daily_quota_consume_blocks_missing_agent_scope() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "deployed_agent_daily_message_quota_consume",
            "record_type": "deployed_agent_daily_message_usage",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "system",
            "actor_role": "system",
            "idempotency_key": "quota_consume_event_001",
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("block"));
        assert!(decision["reason"]
            .as_str()
            .unwrap_or("")
            .contains("missing_agent_id"));
    }

    #[test]
    fn deployed_agent_cost_ledger_write_blocks_quota_denial() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "deployed_agent_cost_ledger_write",
            "record_type": "deployed_agent_monthly_cost_ledger",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "dagent-1",
            "actor_role": "system",
            "agent_id": "dagent-1",
            "run_id": "run-1",
            "idempotency_key": "dagent-1:run-1:cost_ledger",
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": false
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("block"));
        assert_eq!(decision["reason"].as_str(), Some("quota_exceeded"));
    }

    #[test]
    fn hosted_ai_cost_ledger_write_allows_billing_entitled_workspace() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "workspace_hosted_ai_cost_ledger_write",
            "record_type": "workspace_hosted_ai_monthly_cost_ledger",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "system",
            "actor_role": "system",
            "idempotency_key": "request-1:hosted_ai_cost",
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("workspace_hosted_ai_cost_ledger_write")
        );
        assert_eq!(
            decision["record_type"].as_str(),
            Some("workspace_hosted_ai_monthly_cost_ledger")
        );
    }
}

#[cfg(test)]
mod workspace_billing_control_plane_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn workspace_billing_defaults_write_allows_system_defaults() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "workspace_billing_defaults_write",
            "record_type": "workspace_billing",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "system",
            "actor_role": "system",
            "idempotency_key": "workspace-1:billing_defaults",
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("workspace_billing_defaults_write")
        );
        assert_eq!(decision["record_type"].as_str(), Some("workspace_billing"));
    }

    #[test]
    fn workspace_billing_plan_update_blocks_missing_entitlement() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "workspace_billing_plan_update",
            "record_type": "workspace_billing_subscription",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "system",
            "actor_role": "system",
            "idempotency_key": "workspace-1:billing_plan:pro",
            "workspace_access": true,
            "billing_entitled": false,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("block"));
        assert_eq!(
            decision["reason"].as_str(),
            Some("billing_entitlement_missing")
        );
    }

    #[test]
    fn workspace_billing_subscription_write_requires_idempotency_key() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "workspace_billing_subscription_write",
            "record_type": "workspace_billing_subscription",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "system",
            "actor_role": "system",
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("block"));
        assert_eq!(decision["reason"].as_str(), Some("missing_idempotency_key"));
    }
}

#[cfg(test)]
mod security_governance_control_plane_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn security_control_state_write_allows_system_kill_switch_update() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "security_control_state_write",
            "record_type": "security_control_state",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "system",
            "actor_role": "system",
            "scope_type": "workspace",
            "control_kind": "kill_switch",
            "idempotency_key": "workspace:kill_switch:workspace-1:enabled",
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("security_control_state_write")
        );
        assert_eq!(
            decision["record_type"].as_str(),
            Some("security_control_state")
        );
    }

    #[test]
    fn tenant_governance_hold_write_does_not_require_workspace_scope() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "governance_hold_write",
            "record_type": "governance_hold",
            "tenant_id": "tenant-1",
            "workspace_id": "",
            "actor_id": "system",
            "actor_role": "system",
            "scope_type": "tenant",
            "idempotency_key": "tenant:tenant-1:legal_hold:write",
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("governance_hold_write")
        );
    }

    #[test]
    fn governance_hold_release_requires_idempotency_key() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "governance_hold_release",
            "record_type": "governance_hold",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "system",
            "actor_role": "system",
            "scope_type": "workspace",
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("block"));
        assert_eq!(decision["reason"].as_str(), Some("missing_idempotency_key"));
    }
}

#[cfg(test)]
mod channel_user_acquisition_control_plane_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn acquisition_touch_write_allows_scoped_agent_touch() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "channel_user_acquisition_touch_write",
            "record_type": "channel_user_acquisition_touch",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "external-user-1",
            "actor_role": "system",
            "agent_id": "dagent-1",
            "idempotency_key": "dagent-1:telegram:external-user-1:touch",
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("channel_user_acquisition_touch_write")
        );
        assert_eq!(
            decision["record_type"].as_str(),
            Some("channel_user_acquisition_touch")
        );
    }

    #[test]
    fn acquisition_touch_write_blocks_missing_agent_scope() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "channel_user_acquisition_touch_write",
            "record_type": "channel_user_acquisition_touch",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "external-user-1",
            "actor_role": "system",
            "idempotency_key": "touch-1",
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("block"));
        assert!(decision["reason"]
            .as_str()
            .unwrap_or("")
            .contains("missing_agent_id"));
    }

    #[test]
    fn acquisition_conversion_requires_idempotency_key() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "channel_user_acquisition_conversion_write",
            "record_type": "channel_user_acquisition_touch",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "user-1",
            "actor_role": "system",
            "agent_id": "dagent-1",
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("block"));
        assert_eq!(decision["reason"].as_str(), Some("missing_idempotency_key"));
    }
}

#[cfg(test)]
mod deployed_agent_marketplace_control_plane_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn upgrade_click_write_allows_scoped_agent_click() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "deployed_agent_upgrade_click_write",
            "record_type": "deployed_agent_upgrade_click",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "touch-1",
            "actor_role": "system",
            "agent_id": "dagent-1",
            "idempotency_key": "token-hash-1",
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("deployed_agent_upgrade_click_write")
        );
        assert_eq!(
            decision["record_type"].as_str(),
            Some("deployed_agent_upgrade_click")
        );
    }

    #[test]
    fn upgrade_click_write_blocks_missing_agent_scope() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "deployed_agent_upgrade_click_write",
            "record_type": "deployed_agent_upgrade_click",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "touch-1",
            "actor_role": "system",
            "idempotency_key": "token-hash-1",
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("block"));
        assert!(decision["reason"]
            .as_str()
            .unwrap_or("")
            .contains("missing_agent_id"));
    }
}

#[cfg(test)]
mod agent_channel_event_control_plane_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn agent_channel_event_write_allows_idempotent_event() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "agent_channel_event_write",
            "record_type": "agent_channel_event",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "external-user-1",
            "actor_role": "system",
            "agent_id": "dagent-1",
            "idempotency_key": "message-1",
            "webhook_id": "bot-1",
            "webhook_signature_valid": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("agent_channel_event_write")
        );
        assert_eq!(
            decision["record_type"].as_str(),
            Some("agent_channel_event")
        );
    }

    #[test]
    fn agent_channel_event_write_requires_idempotency_key() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "agent_channel_event_write",
            "record_type": "agent_channel_event",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "external-user-1",
            "actor_role": "system",
            "agent_id": "dagent-1",
            "webhook_id": "bot-1",
            "webhook_signature_valid": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("block"));
        assert_eq!(decision["reason"].as_str(), Some("missing_idempotency_key"));
    }
}

#[cfg(test)]
mod personal_context_control_plane_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn personal_context_event_write_allows_idempotent_event() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "personal_context_event_write",
            "record_type": "personal_context_event",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "gmail",
            "actor_role": "system",
            "idempotency_key": "gmail:new_email:msg-1:pctx-1",
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("personal_context_event_write")
        );
        assert_eq!(
            decision["record_type"].as_str(),
            Some("personal_context_event")
        );
    }

    #[test]
    fn personal_context_seen_update_requires_idempotency_key() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "personal_context_event_seen_update",
            "record_type": "personal_context_event",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "sage",
            "actor_role": "system",
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("block"));
        assert_eq!(decision["reason"].as_str(), Some("missing_idempotency_key"));
    }
}

#[cfg(test)]
mod agent_action_event_control_plane_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn agent_action_event_write_allows_idempotent_action_event() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "agent_action_event_write",
            "record_type": "agent_action_event",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "agent-1",
            "actor_role": "system",
            "agent_id": "agent-1",
            "run_id": "run-1",
            "idempotency_key": "action-1",
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("agent_action_event_write")
        );
        assert_eq!(decision["record_type"].as_str(), Some("agent_action_event"));
    }

    #[test]
    fn agent_action_event_write_requires_idempotency_key() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "agent_action_event_write",
            "record_type": "agent_action_event",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "agent-1",
            "actor_role": "system",
            "agent_id": "agent-1",
            "run_id": "run-1",
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("block"));
        assert_eq!(decision["reason"].as_str(), Some("missing_idempotency_key"));
    }
}

#[cfg(test)]
mod control_plane_audit_event_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn agent_secret_access_event_write_allows_idempotent_audit_event() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "agent_secret_access_event_write",
            "record_type": "agent_secret_access_event",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "agent-1",
            "actor_role": "system",
            "run_id": "run-1",
            "idempotency_key": "secret-event-1",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("agent_secret_access_event_write")
        );
        assert_eq!(
            decision["record_type"].as_str(),
            Some("agent_secret_access_event")
        );
    }

    #[test]
    fn agent_secret_access_event_write_requires_idempotency_key() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "agent_secret_access_event_write",
            "record_type": "agent_secret_access_event",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "agent-1",
            "actor_role": "system",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("block"));
        assert_eq!(decision["reason"].as_str(), Some("missing_idempotency_key"));
    }

    #[test]
    fn agent_egress_event_write_allows_idempotent_audit_event() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "agent_egress_event_write",
            "record_type": "agent_egress_event",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "agent-1",
            "actor_role": "system",
            "agent_id": "agent-1",
            "run_id": "run-1",
            "idempotency_key": "egress-event-1",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("agent_egress_event_write")
        );
        assert_eq!(decision["record_type"].as_str(), Some("agent_egress_event"));
    }

    #[test]
    fn activity_ledger_event_write_allows_idempotent_event() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "activity_ledger_event_write",
            "record_type": "activity_ledger_event",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "agent-1",
            "actor_role": "system",
            "run_id": "run-1",
            "thread_id": "thread-1",
            "idempotency_key": "activity-event-1",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("activity_ledger_event_write")
        );
        assert_eq!(
            decision["record_type"].as_str(),
            Some("activity_ledger_event")
        );
    }
}

#[cfg(test)]
mod agent_trace_control_plane_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn agent_trace_create_allows_idempotent_trace() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "agent_trace_create",
            "record_type": "agent_trace",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "agent-1",
            "actor_role": "system",
            "agent_id": "agent-1",
            "run_id": "run-1",
            "thread_id": "thread-1",
            "idempotency_key": "trace-1",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(decision["operation"].as_str(), Some("agent_trace_create"));
        assert_eq!(decision["record_type"].as_str(), Some("agent_trace"));
    }

    #[test]
    fn agent_trace_event_write_allows_idempotent_event() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "agent_trace_event_write",
            "record_type": "agent_trace_event",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "agent-1",
            "actor_role": "system",
            "agent_id": "agent-1",
            "run_id": "run-child-1",
            "idempotency_key": "trace-event-1",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("agent_trace_event_write")
        );
        assert_eq!(decision["record_type"].as_str(), Some("agent_trace_event"));
    }

    #[test]
    fn agent_trace_finish_blocks_missing_idempotency_key() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "agent_trace_finish",
            "record_type": "agent_trace",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "trace-finalizer",
            "actor_role": "system",
            "target_status": "success",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("block"));
        assert_eq!(decision["reason"].as_str(), Some("missing_idempotency_key"));
    }
}

#[cfg(test)]
mod agent_thread_session_turn_control_plane_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn agent_thread_ensure_allows_idempotent_thread_mutation() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "agent_thread_ensure",
            "record_type": "agent_thread",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "user-1",
            "actor_role": "system",
            "thread_id": "thread-1",
            "idempotency_key": "thread-1",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(decision["operation"].as_str(), Some("agent_thread_ensure"));
        assert_eq!(decision["record_type"].as_str(), Some("agent_thread"));
    }

    #[test]
    fn agent_session_upsert_requires_session_id() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "agent_session_upsert",
            "record_type": "agent_session",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "user-1",
            "actor_role": "system",
            "idempotency_key": "session-1",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("block"));
        assert_eq!(decision["reason"].as_str(), Some("missing_session_id"));
    }

    #[test]
    fn agent_session_terminate_allows_terminal_transition() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "agent_session_terminate",
            "record_type": "agent_session",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "session-terminator",
            "actor_role": "system",
            "session_id": "session-1",
            "target_status": "terminated",
            "idempotency_key": "session-1",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("agent_session_terminate")
        );
    }

    #[test]
    fn agent_turn_upsert_requires_thread_id() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "agent_turn_upsert",
            "record_type": "agent_turn",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "agent-1",
            "actor_role": "system",
            "run_id": "run-1",
            "idempotency_key": "request-1",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("block"));
        assert_eq!(decision["reason"].as_str(), Some("missing_thread_id"));
    }

    #[test]
    fn agent_turn_transcript_event_append_allows_idempotent_thread_event() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "agent_turn_transcript_event_append",
            "record_type": "agent_turn",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "run-1",
            "actor_role": "system",
            "thread_id": "thread-1",
            "run_id": "run-1",
            "idempotency_key": "trace-1",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("agent_turn_transcript_event_append")
        );
    }
}

#[cfg(test)]
mod knowledge_control_plane_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn knowledge_source_upsert_allows_idempotent_source_write() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "knowledge_source_upsert",
            "record_type": "knowledge_source",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "agent-1",
            "actor_role": "system",
            "agent_id": "agent-1",
            "idempotency_key": "source-1",
            "target_status": "indexed",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("knowledge_source_upsert")
        );
        assert_eq!(decision["record_type"].as_str(), Some("knowledge_source"));
    }

    #[test]
    fn knowledge_source_chunks_replace_requires_idempotency_key() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "knowledge_source_chunks_replace",
            "record_type": "knowledge_chunks",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "agent-1",
            "actor_role": "system",
            "agent_id": "agent-1",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("block"));
        assert_eq!(decision["reason"].as_str(), Some("missing_idempotency_key"));
    }

    #[test]
    fn knowledge_retrieval_event_write_allows_idempotent_event() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "knowledge_retrieval_event_write",
            "record_type": "knowledge_retrieval_event",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "user-1",
            "actor_role": "system",
            "agent_id": "agent-1",
            "thread_id": "thread-1",
            "run_id": "run-1",
            "idempotency_key": "retrieval-1",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("knowledge_retrieval_event_write")
        );
        assert_eq!(
            decision["record_type"].as_str(),
            Some("knowledge_retrieval_event")
        );
    }
}

#[cfg(test)]
mod workspace_membership_invite_repository_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn membership_update_allows_admin_repository_mutation() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "membership_update",
            "record_type": "workspace_membership",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "user-1",
            "target_actor_id": "user-1",
            "actor_role": "admin",
            "idempotency_key": "workspace-1:user-1",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(decision["operation"].as_str(), Some("membership_update"));
        assert_eq!(
            decision["record_type"].as_str(),
            Some("workspace_membership")
        );
    }

    #[test]
    fn invite_accept_allows_invited_user_mutation() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "invite_accept",
            "record_type": "workspace_member_invite",
            "tenant_id": "invite",
            "workspace_id": "invite",
            "actor_id": "user-1",
            "target_actor_id": "user-1",
            "actor_role": "member",
            "idempotency_key": "invite-1",
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(decision["operation"].as_str(), Some("invite_accept"));
    }

    #[test]
    fn invite_revoke_requires_admin_access() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "invite_revoke",
            "record_type": "workspace_member_invite",
            "tenant_id": "invite",
            "workspace_id": "invite",
            "actor_id": "member-1",
            "actor_role": "member",
            "idempotency_key": "invite-1",
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("block"));
    }

    #[test]
    fn invite_revoke_blocks_accepted_invite() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "invite_revoke",
            "record_type": "workspace_member_invite",
            "tenant_id": "invite",
            "workspace_id": "invite",
            "actor_id": "owner-1",
            "actor_role": "owner",
            "idempotency_key": "invite-1",
            "target_status": "accepted",
            "workspace_access": true,
            "owner_access": true,
            "admin_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("block"));
        assert_eq!(decision["reason"].as_str(), Some("invite_already_accepted"));
    }

    #[test]
    fn invite_revoke_returns_existing_revoked_record() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "invite_revoke",
            "record_type": "workspace_member_invite",
            "tenant_id": "invite",
            "workspace_id": "invite",
            "actor_id": "owner-1",
            "actor_role": "owner",
            "idempotency_key": "invite-1",
            "target_status": "revoked",
            "workspace_access": true,
            "owner_access": true,
            "admin_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["next_action"].as_str(),
            Some("return_existing_control_plane_record")
        );
        assert_eq!(decision["mutation_plan"]["apply"].as_bool(), Some(false));
    }
}

#[cfg(test)]
mod pilot_invite_repository_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn pilot_invite_create_allows_admin_mutation() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "pilot_invite_create",
            "record_type": "pilot_invite",
            "tenant_id": "pilot",
            "workspace_id": "pilot",
            "actor_id": "admin-1",
            "actor_role": "admin",
            "idempotency_key": "ALPHA",
            "target_status": "active",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(decision["operation"].as_str(), Some("pilot_invite_create"));
        assert_eq!(decision["record_type"].as_str(), Some("pilot_invite"));
    }

    #[test]
    fn pilot_invite_claim_allows_idempotent_claim() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "pilot_invite_claim",
            "record_type": "pilot_invite",
            "tenant_id": "pilot",
            "workspace_id": "pilot",
            "actor_id": "pilot-invite-claimer",
            "actor_role": "member",
            "idempotency_key": "ALPHA",
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(decision["operation"].as_str(), Some("pilot_invite_claim"));
    }

    #[test]
    fn pilot_invite_revoke_blocks_non_admin() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "pilot_invite_revoke",
            "record_type": "pilot_invite",
            "tenant_id": "pilot",
            "workspace_id": "pilot",
            "actor_id": "member-1",
            "actor_role": "member",
            "idempotency_key": "pilot-invite-1",
            "target_status": "revoked",
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("block"));
    }
}

#[cfg(test)]
mod identity_control_plane_repository_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn workspace_tenant_binding_requires_admin_access() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "workspace_tenant_binding_ensure",
            "record_type": "workspace_tenant_binding",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "tenant-binding",
            "actor_role": "member",
            "idempotency_key": "tenant-1:workspace-1",
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("block"));
    }

    #[test]
    fn workspace_tenant_binding_allows_admin_mutation() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "workspace_tenant_binding_ensure",
            "record_type": "workspace_tenant_binding",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "tenant-binding",
            "actor_role": "admin",
            "idempotency_key": "tenant-1:workspace-1",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("workspace_tenant_binding_ensure")
        );
        assert_eq!(
            decision["record_type"].as_str(),
            Some("workspace_tenant_binding")
        );
    }

    #[test]
    fn user_profile_update_allows_self_profile_mutation() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "user_profile_update",
            "record_type": "user_profile",
            "tenant_id": "user-profile",
            "workspace_id": "user-profile",
            "actor_id": "user-1",
            "target_actor_id": "user-1",
            "actor_role": "member",
            "idempotency_key": "user-1",
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(decision["operation"].as_str(), Some("user_profile_update"));
        assert_eq!(decision["record_type"].as_str(), Some("user_profile"));
    }
}

#[cfg(test)]
mod agent_registry_workflow_control_plane_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn compiled_workflow_artifact_create_allows_idempotent_compiler_write() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "compiled_workflow_artifact_create",
            "record_type": "compiled_workflow_artifact",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "workflow-compiler",
            "actor_role": "system",
            "idempotency_key": "wf-1:wfver-1",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("compiled_workflow_artifact_create")
        );
        assert_eq!(
            decision["record_type"].as_str(),
            Some("compiled_workflow_artifact")
        );
    }

    #[test]
    fn install_compiled_artifact_update_requires_idempotency_key() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "workspace_agent_install_compiled_artifact_update",
            "record_type": "workspace_agent_install",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "install-1",
            "actor_role": "system",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();

        assert_eq!(decision["decision"].as_str(), Some("block"));
        assert_eq!(decision["reason"].as_str(), Some("missing_idempotency_key"));
    }
}

#[cfg(test)]
mod self_hosted_runtime_control_plane_tests {
    use super::*;
    use serde_json::json;

    fn base_payload(operation: &str) -> serde_json::Value {
        json!({
            "operation": operation,
            "record_type": "runtime_profile",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "node-1",
            "actor_role": "system",
            "idempotency_key": "rprof-1:node-1",
            "owner_access": true,
            "admin_access": true,
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        })
    }

    #[test]
    fn self_hosted_enrollment_intent_create_allows_owner() {
        let mut payload = base_payload("self_hosted_enrollment_intent_create");
        payload["actor_id"] = json!("owner-1");
        payload["actor_role"] = json!("owner");
        let decision = control_plane_service_decision_command(&payload).unwrap();
        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("self_hosted_enrollment_intent_create")
        );
    }

    #[test]
    fn self_hosted_runtime_enroll_allows_idempotent_node_update() {
        let decision =
            control_plane_service_decision_command(&base_payload("self_hosted_runtime_enroll"))
                .unwrap();
        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("self_hosted_runtime_enroll")
        );
    }

    #[test]
    fn self_hosted_runtime_approve_requires_admin_or_owner() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "self_hosted_runtime_approve",
            "record_type": "runtime_profile",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "member-1",
            "actor_role": "member",
            "idempotency_key": "rprof-1",
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();
        assert_eq!(decision["decision"].as_str(), Some("block"));
    }

    #[test]
    fn self_hosted_runtime_heartbeat_requires_idempotency_key() {
        let decision = control_plane_service_decision_command(&json!({
            "operation": "self_hosted_runtime_heartbeat",
            "record_type": "runtime_profile",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "actor_id": "node-1",
            "actor_role": "system",
            "workspace_access": true,
            "billing_entitled": true,
            "quota_ok": true
        }))
        .unwrap();
        assert_eq!(decision["decision"].as_str(), Some("block"));
        assert_eq!(decision["reason"].as_str(), Some("missing_idempotency_key"));
    }

    #[test]
    fn self_hosted_command_enqueue_allows_idempotent_command() {
        let mut payload = base_payload("self_hosted_command_enqueue");
        payload["record_type"] = json!("self_hosted_runtime_command");
        payload["agent_id"] = json!("agent-1");
        payload["idempotency_key"] = json!("shcmd-1");
        let decision = control_plane_service_decision_command(&payload).unwrap();
        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("self_hosted_command_enqueue")
        );
    }

    #[test]
    fn self_hosted_command_claim_allows_idempotent_claim() {
        let mut payload = base_payload("self_hosted_command_claim");
        payload["record_type"] = json!("self_hosted_runtime_command");
        payload["idempotency_key"] = json!("rprof-1:claim-1");
        let decision = control_plane_service_decision_command(&payload).unwrap();
        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("self_hosted_command_claim")
        );
    }

    #[test]
    fn self_hosted_command_complete_allows_terminal_update() {
        let mut payload = base_payload("self_hosted_command_complete");
        payload["record_type"] = json!("self_hosted_runtime_command");
        payload["target_status"] = json!("completed");
        payload["idempotency_key"] = json!("shcmd-1");
        let decision = control_plane_service_decision_command(&payload).unwrap();
        assert_eq!(decision["decision"].as_str(), Some("allow"));
        assert_eq!(
            decision["operation"].as_str(),
            Some("self_hosted_command_complete")
        );
    }
}

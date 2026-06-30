use serde_json::{json, Value};

const SERVICE_OPERATIONS: &[&str] = &[
    "build_policy_payload",
    "validate_contracts",
    "select_provider",
    "ensure_runtime_binding",
    "create_cloud_session",
    "create_self_hosted_session",
    "bind_my_computer_session",
    "execute_cloud_tool",
    "execute_self_hosted_tool",
    "terminate_cloud_session",
    "terminate_self_hosted_session",
    "meter_runtime_usage",
    "collect_artifact",
    "canonicalize_artifact",
    "extend_runtime_session",
    "runtime_audit_event",
    "policy_override_check",
    "kill_state_check",
    "self_hosted_gate",
    "my_computer_gate",
    "customer_live_session",
    "fallback_to_cloud",
    "fallback_to_text",
];

const SESSION_OPERATIONS: &[&str] = &[
    "ensure_runtime_binding",
    "create_cloud_session",
    "create_self_hosted_session",
    "bind_my_computer_session",
    "extend_runtime_session",
    "customer_live_session",
];

const ACTION_OPERATIONS: &[&str] = &[
    "execute_cloud_tool",
    "execute_self_hosted_tool",
    "policy_override_check",
    "kill_state_check",
];

const TERMINATE_OPERATIONS: &[&str] = &[
    "terminate_cloud_session",
    "terminate_self_hosted_session",
    "meter_runtime_usage",
];

const ARTIFACT_OPERATIONS: &[&str] = &["collect_artifact", "canonicalize_artifact"];
const CLOUD_OPERATIONS: &[&str] = &[
    "create_cloud_session",
    "execute_cloud_tool",
    "terminate_cloud_session",
    "fallback_to_cloud",
];
const SELF_HOSTED_OPERATIONS: &[&str] = &[
    "create_self_hosted_session",
    "execute_self_hosted_tool",
    "terminate_self_hosted_session",
    "self_hosted_gate",
];

pub fn deployed_virtual_runtime_service_decision_command(payload: &Value) -> Result<Value, String> {
    let operation = text(payload, "operation", "unknown");
    let tenant_id = payload
        .get("tenant_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let workspace_id = payload
        .get("workspace_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let deployed_agent_id = payload
        .get("deployed_agent_id")
        .or_else(|| payload.get("agent_id"))
        .and_then(Value::as_str)
        .unwrap_or("");
    let session_id = payload
        .get("session_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let runtime_session_id = payload
        .get("runtime_session_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let run_id = payload.get("run_id").and_then(Value::as_str).unwrap_or("");
    let thread_id = payload
        .get("thread_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let actor_id = payload
        .get("actor_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let actor_role = text(payload, "actor_role", "runtime");
    let studio_agent_mode = text(payload, "studio_agent_mode", "text");
    let runtime_binding = text(payload, "runtime_session_binding", "none");
    let runtime_choice = text(payload, "runtime_choice", "virtual_browser");
    let runtime_target = text(payload, "runtime_target", "text");
    let runtime_provider_id = payload
        .get("runtime_provider_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let runtime_profile_id = payload
        .get("runtime_profile_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let runtime_node_id = payload
        .get("runtime_node_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let runtime_attachment_id = payload
        .get("runtime_attachment_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let connector_id = text(payload, "connector_id", "");
    let action_id = text(payload, "action_id", "");
    let recording_policy = text(payload, "session_recording_policy", "metadata_only");
    let filesystem_scope = text(payload, "filesystem_scope", "none");
    let terminal_policy = text(payload, "terminal_command_policy", "blocked");
    let runtime_kill_state = text(payload, "runtime_kill_state", "active");
    let fallback_target = text(payload, "fallback_target", "none");

    let privacy_contract_valid = flag(payload, "privacy_contract_valid", false);
    let computer_safety_contract_valid = flag(payload, "computer_safety_contract_valid", false);
    let runtime_contract_required = flag(
        payload,
        "runtime_contract_required",
        studio_agent_mode != "text",
    );
    let owner_approval_provided = flag(payload, "owner_approval_provided", false);
    let sensitive_action_confirmation =
        flag(payload, "sensitive_action_confirmation_required", true);
    let session_recording_enabled = flag(payload, "session_recording_enabled", false);
    let provider_available = flag(payload, "provider_available", true);
    let runtime_session_bound = flag(payload, "runtime_session_bound", false);
    let runtime_session_healthy = flag(payload, "runtime_session_healthy", true);
    let runtime_registry_available = flag(payload, "runtime_registry_available", true);
    let gateway_healthy = flag(payload, "gateway_healthy", true);
    let self_hosted_node_ready = flag(payload, "self_hosted_node_ready", true);
    let self_hosted_binding_valid = flag(payload, "self_hosted_binding_valid", true);
    let runtime_attachment_ready = flag(payload, "runtime_attachment_ready", true);
    let runtime_concurrency_ok = flag(payload, "runtime_concurrency_ok", true);
    let runtime_quota_ok = flag(payload, "runtime_quota_ok", true);
    let cost_budget_ok = flag(payload, "cost_budget_ok", true);
    let action_supported = flag(payload, "action_supported", true);
    let forbidden_policy_override = flag(payload, "forbidden_policy_override", false);
    let artifact_export_requested = flag(payload, "artifact_export_requested", false);
    let artifact_export_approved = flag(payload, "artifact_export_approved", false);
    let runtime_metered = flag(payload, "runtime_metered", false);
    let kill_switch_enabled = flag(payload, "kill_switch_enabled", false);
    let emergency_stopped = flag(payload, "emergency_stopped", false);
    let customer_live_enabled = flag(payload, "customer_live_enabled", false);
    let fallback_allowed = flag(payload, "fallback_allowed", false);
    let local_gateway_bound = flag(
        payload,
        "local_gateway_bound",
        runtime_binding == "my_computer_agent",
    );
    let domain_allowlist_count = count(payload, "allowed_domain_count");
    let daily_budget_cents = count(payload, "daily_budget_usd_cents");
    let session_timeout_seconds = count(payload, "session_timeout_seconds");
    let max_runtime_seconds = count(payload, "max_runtime_seconds");
    let active_session_count = count(payload, "active_session_count");
    let max_concurrent_sessions = count(payload, "max_concurrent_sessions");

    let known_operation = contains(SERVICE_OPERATIONS, &operation);
    let session_operation = contains(SESSION_OPERATIONS, &operation);
    let action_operation = contains(ACTION_OPERATIONS, &operation);
    let terminate_operation = contains(TERMINATE_OPERATIONS, &operation);
    let artifact_operation = contains(ARTIFACT_OPERATIONS, &operation);
    let cloud_operation = contains(CLOUD_OPERATIONS, &operation);
    let self_hosted_operation = contains(SELF_HOSTED_OPERATIONS, &operation);
    let computer_runtime_mode = studio_agent_mode == "cloud_computer_agent"
        || studio_agent_mode == "my_computer_agent"
        || studio_agent_mode == "self_hosted_agent";
    let requires_runtime_session = action_operation
        || terminate_operation
        || artifact_operation
        || operation == "extend_runtime_session";
    let requires_contracts =
        runtime_contract_required || computer_runtime_mode || operation == "build_policy_payload";
    let requires_provider =
        cloud_operation || operation == "select_provider" || operation == "build_policy_payload";

    let mut block_reasons: Vec<String> = Vec::new();
    let mut approval_reasons: Vec<String> = Vec::new();

    if !known_operation {
        block_reasons.push("unknown_deployed_virtual_runtime_service_operation".to_string());
    }
    if tenant_id.trim().is_empty() {
        block_reasons.push("missing_tenant".to_string());
    }
    if workspace_id.trim().is_empty() {
        block_reasons.push("missing_workspace".to_string());
    }
    if deployed_agent_id.trim().is_empty() {
        block_reasons.push("missing_deployed_agent_id".to_string());
    }
    if kill_switch_enabled || emergency_stopped {
        block_reasons.push("deployed_agent_runtime_kill_state_active".to_string());
    }
    if runtime_kill_state == "paused"
        || runtime_kill_state == "suspended"
        || runtime_kill_state == "archived"
    {
        block_reasons.push("runtime_kill_state_blocks_operation".to_string());
    }
    if requires_contracts && !privacy_contract_valid {
        block_reasons.push("privacy_contract_invalid".to_string());
    }
    if requires_contracts && computer_runtime_mode && !computer_safety_contract_valid {
        block_reasons.push("computer_safety_contract_invalid".to_string());
    }
    if computer_runtime_mode && domain_allowlist_count == 0 {
        block_reasons.push("missing_domain_allowlist".to_string());
    }
    if computer_runtime_mode && daily_budget_cents <= 0 {
        block_reasons.push("missing_runtime_budget".to_string());
    }
    if computer_runtime_mode && session_timeout_seconds <= 0 {
        block_reasons.push("missing_session_timeout".to_string());
    }
    if computer_runtime_mode && max_runtime_seconds <= 0 {
        block_reasons.push("missing_max_runtime_seconds".to_string());
    }
    if computer_runtime_mode && !session_recording_enabled {
        block_reasons.push("session_recording_required".to_string());
    }
    if computer_runtime_mode && recording_policy == "disabled" {
        block_reasons.push("recording_policy_disabled".to_string());
    }
    if computer_runtime_mode && !sensitive_action_confirmation {
        block_reasons.push("sensitive_action_confirmation_missing".to_string());
    }
    if requires_provider && runtime_provider_id.trim().is_empty() {
        block_reasons.push("missing_runtime_provider_id".to_string());
    }
    if requires_provider && !provider_available {
        block_reasons.push("runtime_provider_unavailable".to_string());
    }
    if !runtime_registry_available {
        block_reasons.push("runtime_registry_unavailable".to_string());
    }
    if session_operation
        && max_concurrent_sessions > 0
        && active_session_count >= max_concurrent_sessions
    {
        block_reasons.push("runtime_concurrency_quota_exceeded".to_string());
    }
    if session_operation && !runtime_quota_ok {
        block_reasons.push("runtime_quota_exceeded".to_string());
    }
    if session_operation && !cost_budget_ok {
        block_reasons.push("runtime_cost_budget_exceeded".to_string());
    }
    if requires_runtime_session && runtime_session_id.trim().is_empty() {
        block_reasons.push("missing_runtime_session_id".to_string());
    }
    if requires_runtime_session && !runtime_session_bound {
        block_reasons.push("runtime_session_not_bound".to_string());
    }
    if requires_runtime_session && !runtime_session_healthy {
        block_reasons.push("runtime_session_unhealthy".to_string());
    }
    if action_operation && connector_id.trim().is_empty() {
        block_reasons.push("missing_connector_id".to_string());
    }
    if action_operation && action_id.trim().is_empty() {
        block_reasons.push("missing_action_id".to_string());
    }
    if action_operation && !action_supported {
        block_reasons.push("runtime_action_not_supported".to_string());
    }
    if action_operation && forbidden_policy_override {
        block_reasons.push("forbidden_policy_override".to_string());
    }
    if cloud_operation && studio_agent_mode != "cloud_computer_agent" {
        block_reasons.push("cloud_runtime_requires_cloud_mode".to_string());
    }
    if self_hosted_operation && studio_agent_mode != "self_hosted_agent" {
        block_reasons.push("self_hosted_runtime_requires_self_hosted_mode".to_string());
    }
    if self_hosted_operation && runtime_profile_id.trim().is_empty() {
        block_reasons.push("missing_runtime_profile_id".to_string());
    }
    if self_hosted_operation && runtime_node_id.trim().is_empty() {
        block_reasons.push("missing_runtime_node_id".to_string());
    }
    if self_hosted_operation && runtime_attachment_id.trim().is_empty() {
        block_reasons.push("missing_runtime_attachment_id".to_string());
    }
    if self_hosted_operation && !self_hosted_binding_valid {
        block_reasons.push("invalid_self_hosted_binding".to_string());
    }
    if self_hosted_operation && !self_hosted_node_ready {
        block_reasons.push("self_hosted_node_not_ready".to_string());
    }
    if self_hosted_operation && !runtime_attachment_ready {
        block_reasons.push("runtime_attachment_not_ready".to_string());
    }
    if self_hosted_operation && !runtime_concurrency_ok {
        block_reasons.push("self_hosted_concurrency_exceeded".to_string());
    }
    if operation == "bind_my_computer_session" && !local_gateway_bound {
        block_reasons.push("my_computer_runtime_not_gateway_bound".to_string());
    }
    if operation == "bind_my_computer_session" && !gateway_healthy {
        block_reasons.push("my_computer_gateway_unhealthy".to_string());
    }
    if operation == "customer_live_session" && !customer_live_enabled {
        block_reasons.push("customer_live_not_enabled".to_string());
    }
    if artifact_export_requested && !artifact_export_approved {
        block_reasons.push("artifact_export_not_approved".to_string());
    }
    if operation == "meter_runtime_usage" && runtime_metered {
        block_reasons.push("runtime_usage_already_metered".to_string());
    }
    if operation == "fallback_to_cloud" && !fallback_allowed {
        block_reasons.push("cloud_fallback_not_allowed".to_string());
    }
    if operation == "fallback_to_text" && !fallback_allowed {
        block_reasons.push("text_fallback_not_allowed".to_string());
    }

    if computer_runtime_mode {
        approval_reasons.push("computer_runtime_owner_approval".to_string());
    }
    if action_operation && connector_id == "shell" {
        approval_reasons.push("shell_runtime_action_requires_owner_approval".to_string());
    }
    if artifact_export_requested {
        approval_reasons.push("runtime_artifact_export_requires_approval".to_string());
    }
    if operation == "fallback_to_cloud" {
        approval_reasons.push("cloud_fallback_requires_explicit_approval".to_string());
    }
    if operation == "runtime_audit_event" && actor_role != "owner" && actor_role != "admin" {
        approval_reasons.push("runtime_audit_event_requires_admin_review".to_string());
    }

    let approval_required = !owner_approval_provided && !approval_reasons.is_empty();
    let blocked = !block_reasons.is_empty();
    let decision = if blocked {
        "block"
    } else if approval_required {
        "requires_approval"
    } else {
        "allow"
    };
    let next_action = if blocked {
        "do_not_apply_deployed_virtual_runtime_service_operation"
    } else if approval_required {
        "request_deployed_virtual_runtime_owner_approval"
    } else if operation == "build_policy_payload" {
        "build_deployed_agent_virtual_runtime_payload"
    } else if operation == "validate_contracts" {
        "accept_runtime_contract_snapshot"
    } else if operation == "select_provider" {
        "select_virtual_runtime_provider"
    } else if operation == "ensure_runtime_binding" {
        "ensure_runtime_session_binding"
    } else if operation == "create_cloud_session" || operation == "create_self_hosted_session" {
        "create_bound_runtime_session"
    } else if operation == "bind_my_computer_session" {
        "bind_my_computer_gateway_session"
    } else if action_operation {
        "execute_bound_runtime_tool_call"
    } else if terminate_operation {
        "terminate_and_meter_runtime_session"
    } else if artifact_operation {
        "canonicalize_runtime_artifacts"
    } else if operation == "extend_runtime_session" {
        "extend_runtime_session_metadata"
    } else if operation == "runtime_audit_event" {
        "append_runtime_audit_event"
    } else if operation == "fallback_to_cloud" || operation == "fallback_to_text" {
        "apply_runtime_fallback"
    } else {
        "allow_deployed_virtual_runtime_service_operation"
    };
    let audit_visibility =
        if action_operation || artifact_export_requested || operation == "runtime_audit_event" {
            "security_audit"
        } else if computer_runtime_mode || approval_required || terminate_operation {
            "admin_audit"
        } else {
            "standard_audit"
        };
    let reason = if blocked {
        block_reasons.join(",")
    } else if approval_required {
        approval_reasons.join(",")
    } else {
        "deployed_virtual_runtime_service_operation_allowed".to_string()
    };

    Ok(json!({
        "ok": !blocked,
        "decision": decision,
        "reason": reason,
        "operation": operation,
        "next_action": next_action,
        "approval_required": approval_required,
        "audit_visibility": audit_visibility,
        "scope": {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "deployed_agent_id": deployed_agent_id,
            "session_id": session_id,
            "runtime_session_id": runtime_session_id,
            "run_id": run_id,
            "thread_id": thread_id,
            "actor_id": actor_id,
            "actor_role": actor_role,
        },
        "runtime": {
            "studio_agent_mode": studio_agent_mode,
            "runtime_session_binding": runtime_binding,
            "runtime_choice": runtime_choice,
            "runtime_target": runtime_target,
            "runtime_provider_id": runtime_provider_id,
            "runtime_profile_id": runtime_profile_id,
            "runtime_node_id": runtime_node_id,
            "runtime_attachment_id": runtime_attachment_id,
            "fallback_target": fallback_target,
        },
        "contracts": {
            "privacy_contract_valid": privacy_contract_valid,
            "computer_safety_contract_valid": computer_safety_contract_valid,
            "runtime_contract_required": runtime_contract_required,
            "domain_allowlist_count": domain_allowlist_count,
            "session_recording_enabled": session_recording_enabled,
            "recording_policy": recording_policy,
            "filesystem_scope": filesystem_scope,
            "terminal_policy": terminal_policy,
        },
        "quotas": {
            "daily_budget_usd_cents": daily_budget_cents,
            "session_timeout_seconds": session_timeout_seconds,
            "max_runtime_seconds": max_runtime_seconds,
            "active_session_count": active_session_count,
            "max_concurrent_sessions": max_concurrent_sessions,
            "runtime_quota_ok": runtime_quota_ok,
            "cost_budget_ok": cost_budget_ok,
        },
        "tool_action": {
            "connector_id": connector_id,
            "action_id": action_id,
            "action_supported": action_supported,
            "forbidden_policy_override": forbidden_policy_override,
        },
        "approval": {
            "owner_approval_provided": owner_approval_provided,
            "reasons": approval_reasons,
        },
        "block_reasons": block_reasons,
        "normalized_flags": {
            "known_operation": known_operation,
            "session_operation": session_operation,
            "action_operation": action_operation,
            "terminate_operation": terminate_operation,
            "artifact_operation": artifact_operation,
            "cloud_operation": cloud_operation,
            "self_hosted_operation": self_hosted_operation,
            "computer_runtime_mode": computer_runtime_mode,
            "requires_runtime_session": requires_runtime_session,
            "requires_provider": requires_provider,
        }
    }))
}

fn contains(values: &[&str], needle: &str) -> bool {
    values.iter().any(|value| *value == needle)
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

fn count(payload: &Value, key: &str) -> i64 {
    match payload.get(key) {
        Some(Value::Number(value)) => value.as_i64().unwrap_or(0),
        Some(Value::String(value)) => value.trim().parse::<i64>().unwrap_or(0),
        _ => 0,
    }
}

fn normalize(value: &str) -> String {
    value
        .trim()
        .to_ascii_lowercase()
        .replace('-', "_")
        .replace(' ', "_")
}

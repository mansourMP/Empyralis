use serde_json::{json, Value};

const RUN_SERVICE_OPERATIONS: &[&str] = &[
    "create",
    "start",
    "turn",
    "dispatch",
    "stream_open",
    "stream_event",
    "cancel",
    "retry",
    "resume",
    "approve",
    "reject",
    "finalize_success",
    "finalize_failure",
    "webhook_trigger",
    "child_run_create",
    "delegation_merge",
    "lane_route",
    "status_snapshot",
];

const MUTATING_OPERATIONS: &[&str] = &[
    "create",
    "start",
    "turn",
    "dispatch",
    "cancel",
    "retry",
    "resume",
    "approve",
    "reject",
    "finalize_success",
    "finalize_failure",
    "webhook_trigger",
    "child_run_create",
    "delegation_merge",
    "lane_route",
];

const RUNTIME_REQUIRED_OPERATIONS: &[&str] = &[
    "start",
    "dispatch",
    "retry",
    "resume",
    "child_run_create",
    "lane_route",
];

const APPROVAL_OPERATIONS: &[&str] = &["approve", "reject"];
const TERMINAL_OPERATIONS: &[&str] = &["finalize_success", "finalize_failure"];
const STREAM_OPERATIONS: &[&str] = &["stream_open", "stream_event"];

pub fn run_service_decision_command(payload: &Value) -> Result<Value, String> {
    let operation = text(payload, "operation", "unknown");
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
    let actor_role = text(payload, "actor_role", "member");
    let run_id = payload.get("run_id").and_then(Value::as_str).unwrap_or("");
    let thread_id = payload
        .get("thread_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let parent_run_id = payload
        .get("parent_run_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let workflow_id = payload
        .get("workflow_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let target_agent_id = payload
        .get("target_agent_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let runtime_target = text(payload, "runtime_target", "auto");
    let execution_target = text(payload, "execution_target", "text");
    let trigger_source = text(payload, "trigger_source", "user");
    let run_status = text(payload, "run_status", "pending");
    let stream_id = payload
        .get("stream_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let approval_id = payload
        .get("approval_id")
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
    let budget_profile = text(payload, "budget_profile", "standard");
    let risk_level = first_text(payload, &["risk_level", "risk_decision"], "normal");
    let policy_decision = text(payload, "policy_decision", "allow");

    let workspace_access = flag(payload, "workspace_access", true);
    let owner_access = flag(payload, "owner_access", actor_role == "owner");
    let approval_provided = flag(payload, "approval_provided", false);
    let approval_required_by_policy = flag(payload, "approval_required_by_policy", false);
    let approval_memory_hit = flag(payload, "approval_memory_hit", false);
    let runtime_attached = flag(payload, "runtime_attached", true);
    let runtime_healthy = flag(payload, "runtime_healthy", true);
    let local_confirmation_required = flag(payload, "local_confirmation_required", false);
    let local_confirmation_provided = flag(payload, "local_confirmation_provided", false);
    let browser_required = flag(payload, "browser_required", false);
    let browser_ready = flag(payload, "browser_ready", true);
    let webhook_signature_valid = flag(payload, "webhook_signature_valid", true);
    let kill_switch_enabled = flag(payload, "kill_switch_enabled", false);
    let safe_mode_enabled = flag(payload, "safe_mode_enabled", false);
    let quota_ok = flag(payload, "quota_ok", true);
    let budget_ok = flag(payload, "budget_ok", true);
    let duplicate_request = flag(payload, "duplicate_request", false);
    let terminal_run = flag(payload, "terminal_run", false);
    let retry_allowed = flag(payload, "retry_allowed", true);
    let cancel_allowed = flag(payload, "cancel_allowed", true);
    let resume_allowed = flag(payload, "resume_allowed", true);
    let stream_token_valid = flag(payload, "stream_token_valid", true);
    let workflow_depth_ok = flag(payload, "workflow_depth_ok", true);
    let delegation_merge_ready = flag(payload, "delegation_merge_ready", true);
    let finalization_authorized = flag(payload, "finalization_authorized", true);
    let history_window_ok = flag(payload, "history_window_ok", true);

    let known_operation = contains(RUN_SERVICE_OPERATIONS, &operation);
    let mutating_operation = contains(MUTATING_OPERATIONS, &operation);
    let runtime_required = contains(RUNTIME_REQUIRED_OPERATIONS, &operation);
    let approval_operation = contains(APPROVAL_OPERATIONS, &operation);
    let terminal_operation = contains(TERMINAL_OPERATIONS, &operation);
    let stream_operation = contains(STREAM_OPERATIONS, &operation);
    let external_trigger = trigger_source == "webhook" || operation == "webhook_trigger";
    let child_run_operation = operation == "child_run_create";
    let delegation_operation = operation == "delegation_merge";

    let mut block_reasons: Vec<String> = Vec::new();
    let mut approval_reasons: Vec<String> = Vec::new();

    if !known_operation {
        block_reasons.push("unknown_run_service_operation".to_string());
    }
    if tenant_id.trim().is_empty() {
        block_reasons.push("missing_tenant".to_string());
    }
    if workspace_id.trim().is_empty() {
        block_reasons.push("missing_workspace".to_string());
    }
    if actor_id.trim().is_empty() && !external_trigger {
        block_reasons.push("missing_actor".to_string());
    }
    if !workspace_access {
        block_reasons.push("workspace_access_denied".to_string());
    }
    if kill_switch_enabled && mutating_operation && operation != "cancel" {
        block_reasons.push("run_kill_switch_enabled".to_string());
    }
    if safe_mode_enabled && mutating_operation && !approval_provided {
        approval_reasons.push("safe_mode_run_mutation".to_string());
    }
    if mutating_operation && !quota_ok {
        block_reasons.push("quota_exceeded".to_string());
    }
    if mutating_operation && !budget_ok {
        block_reasons.push("budget_exceeded".to_string());
    }
    if policy_decision == "block" || policy_decision == "deny" || policy_decision == "rejected" {
        block_reasons.push("policy_denied".to_string());
    }
    if risk_level == "block" || risk_level == "deny" || risk_level == "critical_block" {
        block_reasons.push("risk_denied".to_string());
    }
    if requires_approval_for_risk(&risk_level) {
        approval_reasons.push("risk_requires_approval".to_string());
    }
    if approval_required_by_policy && !approval_memory_hit {
        approval_reasons.push("policy_approval_required".to_string());
    }
    if runtime_required && !runtime_attached {
        block_reasons.push("runtime_not_attached".to_string());
    }
    if runtime_required && !runtime_healthy {
        block_reasons.push("runtime_unhealthy".to_string());
    }
    if local_confirmation_required && !local_confirmation_provided {
        approval_reasons.push("local_confirmation_required".to_string());
    }
    if browser_required && !browser_ready {
        block_reasons.push("browser_not_ready".to_string());
    }
    if external_trigger && webhook_id.trim().is_empty() {
        block_reasons.push("missing_webhook_id".to_string());
    }
    if external_trigger && !webhook_signature_valid {
        block_reasons.push("invalid_webhook_signature".to_string());
    }
    if (mutating_operation || external_trigger) && idempotency_key.trim().is_empty() {
        block_reasons.push("missing_idempotency_key".to_string());
    }
    if operation != "create" && operation != "webhook_trigger" && run_id.trim().is_empty() {
        block_reasons.push("missing_run_id".to_string());
    }
    if (operation == "create" || operation == "turn" || operation == "start")
        && thread_id.trim().is_empty()
    {
        block_reasons.push("missing_thread_id".to_string());
    }
    if child_run_operation && parent_run_id.trim().is_empty() {
        block_reasons.push("missing_parent_run_id".to_string());
    }
    if child_run_operation && !workflow_depth_ok {
        block_reasons.push("workflow_depth_exceeded".to_string());
    }
    if delegation_operation && !delegation_merge_ready {
        block_reasons.push("delegation_merge_not_ready".to_string());
    }
    if operation == "retry" && !retry_allowed {
        block_reasons.push("retry_not_allowed".to_string());
    }
    if operation == "cancel" && !cancel_allowed {
        block_reasons.push("cancel_not_allowed".to_string());
    }
    if operation == "resume" && !resume_allowed {
        block_reasons.push("resume_not_allowed".to_string());
    }
    if terminal_run
        && !terminal_operation
        && operation != "status_snapshot"
        && operation != "stream_open"
    {
        block_reasons.push("terminal_run_locked".to_string());
    }
    if terminal_operation && !finalization_authorized {
        block_reasons.push("finalization_not_authorized".to_string());
    }
    if stream_operation && stream_id.trim().is_empty() {
        block_reasons.push("missing_stream_id".to_string());
    }
    if stream_operation && !stream_token_valid {
        block_reasons.push("invalid_stream_token".to_string());
    }
    if approval_operation && approval_id.trim().is_empty() {
        block_reasons.push("missing_approval_id".to_string());
    }
    if operation == "approve" && !owner_access && risk_level == "high" {
        block_reasons.push("high_risk_approval_requires_owner".to_string());
    }
    if (operation == "status_snapshot" || operation == "stream_open") && !history_window_ok {
        block_reasons.push("history_window_denied".to_string());
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
        "do_not_apply_run_service_operation"
    } else if approval_required {
        "request_run_service_approval"
    } else if duplicate_request {
        "return_idempotent_run_result"
    } else if operation == "create" {
        "create_run_record"
    } else if operation == "start" || operation == "dispatch" || operation == "lane_route" {
        "dispatch_run_to_runtime"
    } else if operation == "turn" {
        "append_run_turn"
    } else if stream_operation {
        "open_or_forward_run_stream"
    } else if operation == "cancel" {
        "cancel_run"
    } else if operation == "retry" {
        "retry_run"
    } else if operation == "resume" {
        "resume_run"
    } else if approval_operation {
        "persist_run_approval_decision"
    } else if terminal_operation {
        "finalize_run"
    } else if external_trigger {
        "create_run_from_webhook"
    } else if child_run_operation {
        "create_child_run"
    } else if delegation_operation {
        "merge_delegated_run"
    } else {
        "return_run_status"
    };
    let audit_visibility = if approval_operation
        || terminal_operation
        || risk_level == "high"
        || risk_level == "critical"
    {
        "admin_audit"
    } else if external_trigger || mutating_operation || approval_required {
        "standard_audit"
    } else {
        "read_audit"
    };
    let reason = if blocked {
        block_reasons.join(",")
    } else if approval_required {
        approval_reasons.join(",")
    } else {
        "run_service_operation_allowed".to_string()
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
            "actor_id": actor_id,
            "actor_role": actor_role,
            "run_id": run_id,
            "thread_id": thread_id,
            "parent_run_id": parent_run_id,
            "workflow_id": workflow_id,
            "target_agent_id": target_agent_id,
        },
        "runtime": {
            "runtime_target": runtime_target,
            "execution_target": execution_target,
            "runtime_required": runtime_required,
            "runtime_attached": runtime_attached,
            "runtime_healthy": runtime_healthy,
            "browser_required": browser_required,
            "browser_ready": browser_ready,
        },
        "governance": {
            "risk_level": risk_level,
            "policy_decision": policy_decision,
            "safe_mode_enabled": safe_mode_enabled,
            "kill_switch_enabled": kill_switch_enabled,
            "workspace_access": workspace_access,
            "owner_access": owner_access,
            "history_window_ok": history_window_ok,
        },
        "budget": {
            "budget_profile": budget_profile,
            "quota_ok": quota_ok,
            "budget_ok": budget_ok,
        },
        "request": {
            "trigger_source": trigger_source,
            "webhook_id": webhook_id,
            "idempotency_key_present": !idempotency_key.trim().is_empty(),
            "duplicate_request": duplicate_request,
            "stream_id": stream_id,
            "approval_id": approval_id,
            "run_status": run_status,
        },
        "approval": {
            "provided": approval_provided,
            "memory_hit": approval_memory_hit,
            "reasons": approval_reasons,
        },
        "block_reasons": block_reasons,
        "normalized_flags": {
            "known_operation": known_operation,
            "mutating_operation": mutating_operation,
            "external_trigger": external_trigger,
            "terminal_run": terminal_run,
            "terminal_operation": terminal_operation,
            "child_run_operation": child_run_operation,
            "delegation_operation": delegation_operation,
        }
    }))
}

fn contains(values: &[&str], needle: &str) -> bool {
    values.iter().any(|value| *value == needle)
}

fn first_text(payload: &Value, keys: &[&str], default: &str) -> String {
    for key in keys {
        if let Some(value) = payload.get(*key).and_then(Value::as_str) {
            return normalize(value);
        }
    }
    normalize(default)
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

fn requires_approval_for_risk(risk_level: &str) -> bool {
    matches!(
        risk_level,
        "approval" | "requires_approval" | "high" | "critical" | "privileged"
    )
}

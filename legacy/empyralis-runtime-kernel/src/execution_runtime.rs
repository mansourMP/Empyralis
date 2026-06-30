use serde_json::{json, Value};

const ALLOWED_OPERATIONS: &[&str] = &[
    "select_engine",
    "dispatch_run",
    "execute_run",
    "workflow_node",
    "child_run",
    "connector_action",
    "external_write",
    "usage_metering",
    "cancel_run",
    "resume_run",
    "retry_run",
    "finalize_run",
    "log_event",
];

const TERMINAL_STATUSES: &[&str] = &[
    "succeeded",
    "failed",
    "cancelled",
    "canceled",
    "terminated",
    "expired",
    "archived",
];

const WRITE_ACTION_MARKERS: &[&str] = &[
    "create", "update", "delete", "send", "upload", "move", "post", "comment", "reply", "merge",
    "submit",
];

pub fn execution_runtime_decision_command(payload: &Value) -> Value {
    let operation = normalize_operation(&string_field(payload, &["operation", "action"]));
    let run_id = string_field(payload, &["run_id"]);
    let workspace_id = string_field(payload, &["workspace_id"]);
    let actor_id = string_field(payload, &["actor_id", "user_id", "owner_user_id"]);
    let parent_run_id = string_field(payload, &["parent_run_id"]);
    let workflow_node_id = string_field(payload, &["workflow_node_id", "node_id"]);
    let engine_id = string_field(payload, &["engine_id", "engine", "run_engine"]);
    let execution_target =
        normalize_target(&string_field(payload, &["execution_target", "target"]));
    let runtime_mode =
        normalize_token_or(&string_field(payload, &["runtime_mode", "mode"]), "cloud");
    let status = normalize_token_or(
        &string_field(payload, &["status", "run_status", "current_status"]),
        "queued",
    );
    let connector_id = string_field(payload, &["connector_id", "provider_id"]);
    let connector_action = string_field(payload, &["connector_action", "action_id", "tool_name"]);
    let idempotency_key = string_field(
        payload,
        &["idempotency_key", "write_fingerprint", "stable_fingerprint"],
    );
    let lease_id = string_field(payload, &["lease_id"]);
    let lease_holder_id = string_field(payload, &["lease_holder_id", "worker_id", "machine_id"]);
    let policy_decision = normalize_token_or(
        &string_field(payload, &["policy_decision", "authorization_decision"]),
        "allow",
    );
    let risk_level =
        normalize_token_or(&string_field(payload, &["risk_level", "risk_class"]), "low");
    let workflow_depth = int_field(payload, &["workflow_depth", "turn_depth"], 0);
    let max_workflow_depth =
        int_field(payload, &["max_workflow_depth", "max_turn_depth"], 4).max(1);
    let timeout_seconds = int_field(payload, &["timeout_seconds"], 120);
    let max_timeout_seconds = int_field(payload, &["max_timeout_seconds"], 600).max(1);
    let active_child_runs = int_field(payload, &["active_child_runs"], 0);
    let max_child_runs = int_field(payload, &["max_child_runs"], 8).max(1);
    let active_runtime_runs = int_field(payload, &["active_runtime_runs"], 0);
    let max_runtime_runs = int_field(payload, &["max_runtime_runs"], 32).max(1);
    let usage_unit_count = int_field(payload, &["usage_unit_count", "credits", "units"], 0);
    let max_usage_units = int_field(payload, &["max_usage_units"], 10_000).max(1);
    let engine_available = bool_field(payload, &["engine_available"], true);
    let runtime_attached = bool_field(
        payload,
        &["runtime_attached", "local_runtime_attached"],
        false,
    );
    let lease_required = bool_field(
        payload,
        &["lease_required"],
        matches!(execution_target.as_str(), "local_companion" | "this_device"),
    );
    let lease_valid = bool_field(payload, &["lease_valid"], false);
    let approval_provided = bool_field(
        payload,
        &["approval_provided", "human_approval_provided"],
        false,
    );
    let approval_required = bool_field(payload, &["approval_required", "requires_approval"], false);
    let kill_switch_enabled = bool_field(payload, &["kill_switch_enabled"], false);
    let safe_mode_enabled = bool_field(payload, &["safe_mode_enabled"], false);
    let entitlement_ok = bool_field(payload, &["entitlement_ok", "workspace_entitled"], true);
    let budget_available = bool_field(payload, &["budget_available", "budget_ok"], true);
    let metering_enabled = bool_field(payload, &["metering_enabled"], true);
    let external_write_requested = bool_field(
        payload,
        &["external_write", "external_write_requested"],
        false,
    ) || operation == "external_write"
        || write_like_action(&connector_action);

    let mut block_reasons: Vec<String> = Vec::new();
    let mut approval_reasons: Vec<String> = Vec::new();
    let mut normalized_flags: Vec<&str> = Vec::new();

    if operation.is_empty() {
        block_reasons.push("execution_runtime_operation_missing".to_string());
    } else if !ALLOWED_OPERATIONS.contains(&operation.as_str()) {
        block_reasons.push(format!("unknown_execution_runtime_operation:{operation}"));
    }
    if requires_run_id(&operation) && run_id.is_empty() {
        block_reasons.push("run_id_missing".to_string());
    }
    if requires_workspace_id(&operation) && workspace_id.is_empty() {
        block_reasons.push("workspace_id_missing".to_string());
    }
    if requires_actor_id(&operation) && actor_id.is_empty() {
        block_reasons.push("actor_id_missing".to_string());
    }
    if kill_switch_enabled
        && !matches!(
            operation.as_str(),
            "cancel_run" | "finalize_run" | "log_event"
        )
    {
        block_reasons.push("kill_switch_blocks_execution_runtime".to_string());
    }
    if safe_mode_enabled && external_write_requested && !approval_provided {
        approval_reasons.push("safe_mode_external_write_requires_approval".to_string());
    }
    if terminal_status(&status)
        && matches!(
            operation.as_str(),
            "dispatch_run"
                | "execute_run"
                | "workflow_node"
                | "child_run"
                | "connector_action"
                | "external_write"
                | "resume_run"
        )
    {
        block_reasons.push("terminal_run_cannot_execute".to_string());
    }
    if matches!(
        operation.as_str(),
        "select_engine" | "dispatch_run" | "execute_run"
    ) {
        if engine_id.is_empty() {
            block_reasons.push("engine_id_missing".to_string());
        } else if !engine_available {
            block_reasons.push("engine_unavailable".to_string());
        }
    }
    if active_runtime_runs >= max_runtime_runs
        && matches!(operation.as_str(), "dispatch_run" | "execute_run")
    {
        block_reasons.push("runtime_concurrency_exhausted".to_string());
    }
    if !entitlement_ok
        && matches!(
            operation.as_str(),
            "dispatch_run" | "execute_run" | "child_run" | "connector_action"
        )
    {
        block_reasons.push("workspace_entitlement_missing".to_string());
    }
    if !budget_available
        && matches!(
            operation.as_str(),
            "dispatch_run" | "execute_run" | "child_run" | "usage_metering"
        )
    {
        block_reasons.push("runtime_budget_exhausted".to_string());
    }
    if local_target(&execution_target)
        && !runtime_attached
        && matches!(
            operation.as_str(),
            "dispatch_run" | "execute_run" | "connector_action"
        )
    {
        block_reasons.push("local_runtime_not_attached".to_string());
    }
    if lease_required
        && matches!(
            operation.as_str(),
            "dispatch_run" | "execute_run" | "connector_action"
        )
    {
        if lease_id.is_empty() || lease_holder_id.is_empty() {
            block_reasons.push("execution_lease_missing".to_string());
        } else if !lease_valid {
            block_reasons.push("execution_lease_invalid".to_string());
        }
    }
    if matches!(operation.as_str(), "workflow_node" | "child_run") {
        if workflow_node_id.is_empty() {
            block_reasons.push("workflow_node_id_missing".to_string());
        }
        if operation == "child_run" && parent_run_id.is_empty() {
            block_reasons.push("parent_run_id_missing".to_string());
        }
        if workflow_depth >= max_workflow_depth {
            block_reasons.push("workflow_depth_exceeded".to_string());
        }
        if active_child_runs >= max_child_runs {
            block_reasons.push("child_run_concurrency_exhausted".to_string());
        }
    }
    if matches!(operation.as_str(), "connector_action" | "external_write") {
        if connector_id.is_empty() {
            block_reasons.push("connector_id_missing".to_string());
        }
        if connector_action.is_empty() {
            block_reasons.push("connector_action_missing".to_string());
        }
        if policy_decision == "block" {
            block_reasons.push("policy_blocks_connector_action".to_string());
        }
        if policy_decision == "require_approval" || approval_required || elevated_risk(&risk_level)
        {
            approval_reasons.push("connector_action_requires_approval".to_string());
        }
        if external_write_requested && idempotency_key.is_empty() {
            block_reasons.push("external_write_idempotency_missing".to_string());
        }
    }
    if operation == "usage_metering" {
        if !metering_enabled {
            block_reasons.push("usage_metering_disabled".to_string());
        }
        if usage_unit_count <= 0 {
            block_reasons.push("usage_units_missing".to_string());
        } else if usage_unit_count > max_usage_units {
            approval_reasons.push("usage_units_exceed_default_limit".to_string());
        }
    }
    if timeout_seconds <= 0 {
        block_reasons.push("timeout_must_be_positive".to_string());
    } else if timeout_seconds > 7_200 {
        block_reasons.push("timeout_exceeds_hard_limit".to_string());
    } else if timeout_seconds > max_timeout_seconds {
        approval_reasons.push("timeout_exceeds_default_limit".to_string());
    }
    if external_write_requested {
        normalized_flags.push("external_write");
    }
    if local_target(&execution_target) {
        normalized_flags.push("local_runtime");
    }
    if lease_required {
        normalized_flags.push("lease_required");
    }
    if metering_enabled {
        normalized_flags.push("metering_enabled");
    }

    block_reasons.sort();
    block_reasons.dedup();
    approval_reasons.sort();
    approval_reasons.dedup();
    normalized_flags.sort();
    normalized_flags.dedup();

    let decision = if !block_reasons.is_empty() {
        "block"
    } else if !approval_reasons.is_empty() && !approval_provided {
        "require_approval"
    } else {
        "allow"
    };
    let reason = if !block_reasons.is_empty() {
        block_reasons.join("; ")
    } else if decision == "require_approval" {
        approval_reasons.join("; ")
    } else {
        "execution_runtime_policy_satisfied".to_string()
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
        "normalized_status": normalized_status(&operation, &status),
        "run": {
            "run_id": run_id,
            "workspace_id": workspace_id,
            "actor_id": actor_id,
            "parent_run_id": parent_run_id,
            "workflow_node_id": workflow_node_id,
            "status": status,
        },
        "execution": {
            "engine_id": engine_id,
            "engine_available": engine_available,
            "execution_target": execution_target,
            "runtime_mode": runtime_mode,
            "runtime_attached": runtime_attached,
            "timeout_seconds": timeout_seconds.max(1).min(max_timeout_seconds),
            "external_write_requested": external_write_requested,
        },
        "lease": {
            "required": lease_required,
            "lease_id": lease_id,
            "holder_id": lease_holder_id,
            "valid": lease_valid,
        },
        "workflow": {
            "depth": workflow_depth,
            "max_depth": max_workflow_depth,
            "active_child_runs": active_child_runs,
            "max_child_runs": max_child_runs,
        },
        "connector": {
            "connector_id": connector_id,
            "action": connector_action,
            "policy_decision": policy_decision,
            "risk_level": risk_level,
            "idempotency_key_present": !idempotency_key.is_empty(),
        },
        "metering": {
            "enabled": metering_enabled,
            "usage_unit_count": usage_unit_count,
            "max_usage_units": max_usage_units,
        },
        "normalized_flags": normalized_flags,
        "block_reasons": block_reasons,
        "approval_reasons": approval_reasons,
    })
}

fn string_field(payload: &Value, keys: &[&str]) -> String {
    for key in keys {
        if let Some(value) = payload.get(*key) {
            if let Some(text) = value.as_str() {
                let trimmed = text.trim();
                if !trimmed.is_empty() {
                    return trimmed.to_string();
                }
            }
        }
    }
    String::new()
}

fn bool_field(payload: &Value, keys: &[&str], default: bool) -> bool {
    for key in keys {
        if let Some(value) = payload.get(*key) {
            if let Some(flag) = value.as_bool() {
                return flag;
            }
            if let Some(text) = value.as_str() {
                return matches!(
                    normalize_token(text).as_str(),
                    "1" | "true" | "yes" | "on" | "enabled" | "allow" | "allowed"
                );
            }
            if let Some(number) = value.as_i64() {
                return number > 0;
            }
        }
    }
    default
}

fn int_field(payload: &Value, keys: &[&str], default: i64) -> i64 {
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

fn normalize_token(value: &str) -> String {
    value
        .trim()
        .to_ascii_lowercase()
        .replace('-', "_")
        .replace(' ', "_")
}

fn normalize_token_or(value: &str, fallback: &str) -> String {
    let token = normalize_token(value);
    if token.is_empty() {
        fallback.to_string()
    } else {
        token
    }
}

fn normalize_operation(value: &str) -> String {
    match normalize_token(value).as_str() {
        "engine" | "engine_selection" | "select_engine" => "select_engine".to_string(),
        "dispatch" | "dispatch_run" | "start" | "start_run" => "dispatch_run".to_string(),
        "execute" | "execute_run" | "run" => "execute_run".to_string(),
        "node" | "workflow_node" | "run_node" => "workflow_node".to_string(),
        "child" | "child_run" | "delegate" | "delegate_child" => "child_run".to_string(),
        "connector" | "connector_action" | "tool_action" => "connector_action".to_string(),
        "external_write" | "write_once" | "external_write_once" => "external_write".to_string(),
        "meter" | "meter_usage" | "usage" | "usage_metering" => "usage_metering".to_string(),
        "cancel" | "cancel_run" => "cancel_run".to_string(),
        "resume" | "resume_run" => "resume_run".to_string(),
        "retry" | "retry_run" => "retry_run".to_string(),
        "finalize" | "finalize_run" | "complete" => "finalize_run".to_string(),
        "log" | "log_event" | "emit_log" => "log_event".to_string(),
        "" => String::new(),
        other => other.to_string(),
    }
}

fn normalize_target(value: &str) -> String {
    match normalize_token(value).as_str() {
        "local" | "local_companion" | "this_device" | "my_computer" => {
            "local_companion".to_string()
        }
        "cloud" | "hosted" | "cloud_runtime" => "cloud".to_string(),
        "cloud_computer" | "virtual_computer" => "cloud_computer".to_string(),
        "server" | "vps" | "self_hosted" => "self_hosted".to_string(),
        "" => "cloud".to_string(),
        other => other.to_string(),
    }
}

fn requires_run_id(operation: &str) -> bool {
    !matches!(operation, "" | "select_engine" | "log_event")
}

fn requires_workspace_id(operation: &str) -> bool {
    !matches!(operation, "" | "select_engine" | "log_event")
}

fn requires_actor_id(operation: &str) -> bool {
    matches!(
        operation,
        "dispatch_run"
            | "execute_run"
            | "connector_action"
            | "external_write"
            | "cancel_run"
            | "resume_run"
            | "retry_run"
    )
}

fn terminal_status(status: &str) -> bool {
    TERMINAL_STATUSES.contains(&status)
}

fn local_target(target: &str) -> bool {
    matches!(target, "local_companion" | "this_device")
}

fn elevated_risk(risk_level: &str) -> bool {
    matches!(risk_level, "high" | "critical" | "orange" | "red")
}

fn write_like_action(action: &str) -> bool {
    let normalized = normalize_token(action);
    WRITE_ACTION_MARKERS
        .iter()
        .any(|marker| normalized.contains(marker))
}

fn next_action(operation: &str, decision: &str) -> &'static str {
    if decision == "block" {
        return "stop_execution_runtime";
    }
    if decision == "require_approval" {
        return "request_execution_runtime_approval";
    }
    match operation {
        "select_engine" => "select_registered_engine",
        "dispatch_run" => "dispatch_runtime_run",
        "execute_run" => "execute_runtime_run",
        "workflow_node" => "execute_workflow_node",
        "child_run" => "dispatch_child_run",
        "connector_action" => "execute_connector_action",
        "external_write" => "execute_external_write_once",
        "usage_metering" => "record_usage_event",
        "cancel_run" => "cancel_runtime_run",
        "resume_run" => "resume_runtime_run",
        "retry_run" => "retry_runtime_run",
        "finalize_run" => "finalize_runtime_run",
        "log_event" => "emit_runtime_log",
        _ => "review_execution_runtime",
    }
}

fn normalized_status<'a>(operation: &str, status: &'a str) -> &'a str {
    if operation != "finalize_run" {
        return status;
    }
    match status {
        "success" | "succeeded" | "completed" => "completed",
        "timed_out" | "timeout" | "expired" => "timeout",
        "cancelled" | "canceled" => "cancelled",
        "failed" | "error" | "signaled" | "terminated" => "failed",
        other => other,
    }
}

#[cfg(test)]
mod execution_runtime_kernel_status_tests {
    use super::execution_runtime_decision_command;

    #[test]
    fn finalize_run_normalizes_succeeded_status() {
        let decision = execution_runtime_decision_command(&serde_json::json!({
            "operation": "finalize_run",
            "run_id": "run-1",
            "workspace_id": "ws-1",
            "status": "succeeded"
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("allow")
        );
        assert_eq!(
            decision
                .get("normalized_status")
                .and_then(|value| value.as_str()),
            Some("completed")
        );
    }

    #[test]
    fn finalize_run_normalizes_timed_out_status() {
        let decision = execution_runtime_decision_command(&serde_json::json!({
            "operation": "finalize_run",
            "run_id": "run-1",
            "workspace_id": "ws-1",
            "status": "timed_out"
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("allow")
        );
        assert_eq!(
            decision
                .get("normalized_status")
                .and_then(|value| value.as_str()),
            Some("timeout")
        );
    }
}

use crate::policy::normalize_token;
use crate::protocol;
use serde_json::{json, Value};

const DEFAULT_MAX_RUNTIME_SECONDS: u64 = 120;
const DEFAULT_GRACE_SECONDS: u64 = 5;
const MAX_GRACE_SECONDS: u64 = 60;

pub fn process_lifecycle_decision_command(input: &Value) -> Value {
    let operation = normalize_operation(
        protocol::string_field(input, "operation")
            .or_else(|| protocol::string_field(input, "action"))
            .as_deref()
            .unwrap_or(""),
    );
    if operation.is_empty() {
        return protocol::block("process_operation_missing");
    }

    let run_id = protocol::string_field(input, "run_id");
    let process_id = protocol::string_field(input, "process_id")
        .or_else(|| protocol::string_field(input, "pid"));
    let current_status = normalize_status(
        protocol::string_field(input, "status")
            .or_else(|| protocol::string_field(input, "current_status"))
            .as_deref()
            .unwrap_or("unknown"),
    );
    let elapsed_seconds = input
        .get("elapsed_seconds")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let max_runtime_seconds = input
        .get("max_runtime_seconds")
        .and_then(Value::as_u64)
        .unwrap_or(DEFAULT_MAX_RUNTIME_SECONDS)
        .max(1);
    let grace_seconds = input
        .get("grace_seconds")
        .and_then(Value::as_u64)
        .unwrap_or(DEFAULT_GRACE_SECONDS)
        .clamp(0, MAX_GRACE_SECONDS);
    let kill_switch_enabled = boolish(input, "kill_switch_enabled");
    let force = boolish(input, "force");

    if kill_switch_enabled && operation == "start" {
        return lifecycle_response(
            "block",
            "kill_switch_blocks_process_start",
            &operation,
            run_id,
            process_id,
            &current_status,
            elapsed_seconds,
            max_runtime_seconds,
            grace_seconds,
            "none",
            false,
        );
    }

    match operation.as_str() {
        "start" => start_decision(
            run_id,
            process_id,
            &current_status,
            elapsed_seconds,
            max_runtime_seconds,
            grace_seconds,
        ),
        "heartbeat" => heartbeat_decision(
            run_id,
            process_id,
            &current_status,
            elapsed_seconds,
            max_runtime_seconds,
            grace_seconds,
        ),
        "terminate" => terminate_decision(
            run_id,
            process_id,
            &current_status,
            elapsed_seconds,
            max_runtime_seconds,
            grace_seconds,
            force,
        ),
        "cancel" => cancel_decision(
            run_id,
            process_id,
            &current_status,
            elapsed_seconds,
            max_runtime_seconds,
            grace_seconds,
            force,
        ),
        "timeout" => timeout_decision(
            run_id,
            process_id,
            &current_status,
            elapsed_seconds,
            max_runtime_seconds,
            grace_seconds,
        ),
        "cleanup" => cleanup_decision(
            run_id,
            process_id,
            &current_status,
            elapsed_seconds,
            max_runtime_seconds,
            grace_seconds,
        ),
        _ => protocol::block("unknown_process_operation"),
    }
}

fn start_decision(
    run_id: Option<String>,
    process_id: Option<String>,
    current_status: &str,
    elapsed_seconds: u64,
    max_runtime_seconds: u64,
    grace_seconds: u64,
) -> Value {
    if run_id.is_none() {
        return protocol::block("run_id_missing");
    }
    if matches!(current_status, "running" | "starting") {
        return lifecycle_response(
            "block",
            "process_already_active",
            "start",
            run_id,
            process_id,
            current_status,
            elapsed_seconds,
            max_runtime_seconds,
            grace_seconds,
            "none",
            false,
        );
    }
    lifecycle_response(
        "allow",
        "process_start_allowed",
        "start",
        run_id,
        process_id,
        current_status,
        elapsed_seconds,
        max_runtime_seconds,
        grace_seconds,
        "spawn",
        false,
    )
}

fn heartbeat_decision(
    run_id: Option<String>,
    process_id: Option<String>,
    current_status: &str,
    elapsed_seconds: u64,
    max_runtime_seconds: u64,
    grace_seconds: u64,
) -> Value {
    if process_id.is_none() {
        return protocol::block("process_id_missing");
    }
    if !matches!(current_status, "running" | "starting") {
        return lifecycle_response(
            "block",
            "process_not_active",
            "heartbeat",
            run_id,
            process_id,
            current_status,
            elapsed_seconds,
            max_runtime_seconds,
            grace_seconds,
            "none",
            false,
        );
    }
    if elapsed_seconds > max_runtime_seconds {
        return lifecycle_response(
            "require_approval",
            "process_runtime_exceeded",
            "heartbeat",
            run_id,
            process_id,
            current_status,
            elapsed_seconds,
            max_runtime_seconds,
            grace_seconds,
            "terminate",
            true,
        );
    }
    lifecycle_response(
        "allow",
        "process_heartbeat_allowed",
        "heartbeat",
        run_id,
        process_id,
        current_status,
        elapsed_seconds,
        max_runtime_seconds,
        grace_seconds,
        "continue",
        false,
    )
}

fn terminate_decision(
    run_id: Option<String>,
    process_id: Option<String>,
    current_status: &str,
    elapsed_seconds: u64,
    max_runtime_seconds: u64,
    grace_seconds: u64,
    force: bool,
) -> Value {
    if process_id.is_none() {
        return protocol::block("process_id_missing");
    }
    if matches!(
        current_status,
        "succeeded" | "failed" | "cancelled" | "terminated"
    ) {
        return lifecycle_response(
            "allow",
            "process_already_terminal",
            "terminate",
            run_id,
            process_id,
            current_status,
            elapsed_seconds,
            max_runtime_seconds,
            grace_seconds,
            "cleanup",
            false,
        );
    }
    lifecycle_response(
        if force { "require_approval" } else { "allow" },
        if force {
            "forced_termination_requires_approval"
        } else {
            "process_termination_allowed"
        },
        "terminate",
        run_id,
        process_id,
        current_status,
        elapsed_seconds,
        max_runtime_seconds,
        grace_seconds,
        if force { "kill" } else { "terminate" },
        force,
    )
}

fn cancel_decision(
    run_id: Option<String>,
    process_id: Option<String>,
    current_status: &str,
    elapsed_seconds: u64,
    max_runtime_seconds: u64,
    grace_seconds: u64,
    force: bool,
) -> Value {
    if run_id.is_none() {
        return protocol::block("run_id_missing");
    }
    if matches!(
        current_status,
        "succeeded" | "failed" | "cancelled" | "terminated"
    ) {
        return lifecycle_response(
            "allow",
            "process_already_terminal",
            "cancel",
            run_id,
            process_id,
            current_status,
            elapsed_seconds,
            max_runtime_seconds,
            grace_seconds,
            "cleanup",
            false,
        );
    }
    let has_process_id = process_id.is_some();
    lifecycle_response(
        if force { "require_approval" } else { "allow" },
        if force {
            "forced_cancel_requires_approval"
        } else {
            "process_cancel_allowed"
        },
        "cancel",
        run_id,
        process_id,
        current_status,
        elapsed_seconds,
        max_runtime_seconds,
        grace_seconds,
        if has_process_id {
            "terminate"
        } else {
            "mark_cancelled"
        },
        force,
    )
}

fn timeout_decision(
    run_id: Option<String>,
    process_id: Option<String>,
    current_status: &str,
    elapsed_seconds: u64,
    max_runtime_seconds: u64,
    grace_seconds: u64,
) -> Value {
    if elapsed_seconds <= max_runtime_seconds {
        return lifecycle_response(
            "allow",
            "process_within_runtime_limit",
            "timeout",
            run_id,
            process_id,
            current_status,
            elapsed_seconds,
            max_runtime_seconds,
            grace_seconds,
            "continue",
            false,
        );
    }
    lifecycle_response(
        "allow",
        "process_timeout_enforced",
        "timeout",
        run_id,
        process_id,
        current_status,
        elapsed_seconds,
        max_runtime_seconds,
        grace_seconds,
        "terminate",
        true,
    )
}

fn cleanup_decision(
    run_id: Option<String>,
    process_id: Option<String>,
    current_status: &str,
    elapsed_seconds: u64,
    max_runtime_seconds: u64,
    grace_seconds: u64,
) -> Value {
    lifecycle_response(
        "allow",
        "process_cleanup_allowed",
        "cleanup",
        run_id,
        process_id,
        current_status,
        elapsed_seconds,
        max_runtime_seconds,
        grace_seconds,
        "cleanup",
        false,
    )
}

fn lifecycle_response(
    decision: &str,
    reason: &str,
    operation: &str,
    run_id: Option<String>,
    process_id: Option<String>,
    current_status: &str,
    elapsed_seconds: u64,
    max_runtime_seconds: u64,
    grace_seconds: u64,
    next_action: &str,
    terminal_transition: bool,
) -> Value {
    json!({
        "ok": decision != "block",
        "decision": decision,
        "reason": reason,
        "operation": operation,
        "run_id": run_id,
        "process_id": process_id,
        "current_status": current_status,
        "elapsed_seconds": elapsed_seconds,
        "max_runtime_seconds": max_runtime_seconds,
        "grace_seconds": grace_seconds,
        "next_action": next_action,
        "terminal_transition": terminal_transition,
        "approval_required": decision == "require_approval",
        "cacheable": false,
        "audit_visibility": if decision == "allow" && !terminal_transition { "standard" } else { "security" },
    })
}

fn normalize_operation(value: &str) -> String {
    match normalize_token(value).as_str() {
        "start" | "spawn" | "launch" => "start".to_string(),
        "heartbeat" | "ping" | "tick" => "heartbeat".to_string(),
        "terminate" | "stop" | "kill" => "terminate".to_string(),
        "cancel" | "abort" => "cancel".to_string(),
        "timeout" | "expire" => "timeout".to_string(),
        "cleanup" | "reap" => "cleanup".to_string(),
        _ => String::new(),
    }
}

fn normalize_status(value: &str) -> String {
    match normalize_token(value).as_str() {
        "queued" | "pending" => "queued".to_string(),
        "starting" | "spawning" => "starting".to_string(),
        "running" | "active" => "running".to_string(),
        "succeeded" | "success" | "done" => "succeeded".to_string(),
        "failed" | "error" => "failed".to_string(),
        "cancelled" | "canceled" => "cancelled".to_string(),
        "terminated" | "killed" => "terminated".to_string(),
        _ => "unknown".to_string(),
    }
}

fn boolish(input: &Value, key: &str) -> bool {
    match input.get(key) {
        Some(Value::Bool(value)) => *value,
        Some(Value::String(value)) => matches!(
            normalize_token(value).as_str(),
            "1" | "true" | "yes" | "on" | "enabled" | "active" | "force" | "forced"
        ),
        Some(Value::Number(value)) => value.as_u64().unwrap_or(0) > 0,
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_operation_blocks() {
        let response = process_lifecycle_decision_command(&json!({}));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "process_operation_missing");
    }

    #[test]
    fn start_requires_run_id() {
        let response = process_lifecycle_decision_command(&json!({
            "operation": "start"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "run_id_missing");
    }

    #[test]
    fn start_allows_queued_run() {
        let response = process_lifecycle_decision_command(&json!({
            "operation": "start",
            "run_id": "run-1",
            "status": "queued"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_action"], "spawn");
    }

    #[test]
    fn heartbeat_over_runtime_requires_approval_to_terminate() {
        let response = process_lifecycle_decision_command(&json!({
            "operation": "heartbeat",
            "process_id": "pid-1",
            "status": "running",
            "elapsed_seconds": 200,
            "max_runtime_seconds": 120
        }));
        assert_eq!(response["decision"], "require_approval");
        assert_eq!(response["next_action"], "terminate");
    }

    #[test]
    fn forced_termination_requires_approval() {
        let response = process_lifecycle_decision_command(&json!({
            "operation": "terminate",
            "process_id": "pid-1",
            "status": "running",
            "force": true
        }));
        assert_eq!(response["decision"], "require_approval");
        assert_eq!(response["reason"], "forced_termination_requires_approval");
    }

    #[test]
    fn kill_switch_blocks_start() {
        let response = process_lifecycle_decision_command(&json!({
            "operation": "start",
            "run_id": "run-1",
            "kill_switch_enabled": true
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "kill_switch_blocks_process_start");
    }
}

#[cfg(test)]
mod process_lifecycle_kernel_boundary_tests {
    use super::process_lifecycle_decision_command;

    #[test]
    fn kill_switch_blocks_process_start_before_spawn() {
        let decision = process_lifecycle_decision_command(&serde_json::json!({
            "operation": "start",
            "run_id": "anthropic-local-cli-login",
            "status": "not_started",
            "kill_switch_enabled": true
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("block")
        );
        assert_eq!(
            decision.get("reason").and_then(|value| value.as_str()),
            Some("kill_switch_blocks_process_start")
        );
    }

    #[test]
    fn process_start_requires_run_identity() {
        let decision = process_lifecycle_decision_command(&serde_json::json!({
            "operation": "start",
            "status": "not_started"
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("block")
        );
        assert_eq!(
            decision.get("reason").and_then(|value| value.as_str()),
            Some("run_id_missing")
        );
    }
}

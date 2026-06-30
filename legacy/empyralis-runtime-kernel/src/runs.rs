use crate::policy::normalize_token;
use crate::protocol;
use serde_json::{json, Value};

const DEFAULT_MAX_ATTEMPTS: u64 = 3;
const DEFAULT_MAX_BUDGET_CENTS: u64 = 1_000;
const DEFAULT_MAX_RUNTIME_SECONDS: u64 = 600;

pub fn run_orchestration_decision_command(input: &Value) -> Value {
    let operation = normalize_operation(
        protocol::string_field(input, "operation")
            .or_else(|| protocol::string_field(input, "action"))
            .as_deref()
            .unwrap_or(""),
    );
    if operation.is_empty() {
        return protocol::block("run_operation_missing");
    }

    let run_id = protocol::string_field(input, "run_id");
    let workspace_id = protocol::string_field(input, "workspace_id");
    let status = normalize_status(
        protocol::string_field(input, "status")
            .or_else(|| protocol::string_field(input, "current_status"))
            .as_deref()
            .unwrap_or("new"),
    );
    let mode = normalize_mode(
        protocol::string_field(input, "mode")
            .or_else(|| protocol::string_field(input, "run_mode"))
            .as_deref()
            .unwrap_or("interactive"),
    );
    let priority = normalize_priority(
        protocol::string_field(input, "priority")
            .as_deref()
            .unwrap_or("normal"),
    );
    let idempotency_key = protocol::string_field(input, "idempotency_key");
    let prompt = protocol::string_field(input, "prompt")
        .or_else(|| protocol::string_field(input, "task"))
        .or_else(|| protocol::string_field(input, "user_message"));
    let attempts = input.get("attempts").and_then(Value::as_u64).unwrap_or(0);
    let max_attempts = input
        .get("max_attempts")
        .and_then(Value::as_u64)
        .unwrap_or(DEFAULT_MAX_ATTEMPTS)
        .max(1);
    let requested_budget_cents = input
        .get("requested_budget_cents")
        .or_else(|| input.get("budget_cents"))
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let max_budget_cents = input
        .get("max_budget_cents")
        .and_then(Value::as_u64)
        .unwrap_or(DEFAULT_MAX_BUDGET_CENTS)
        .max(1);
    let requested_runtime_seconds = input
        .get("requested_runtime_seconds")
        .or_else(|| input.get("max_runtime_seconds"))
        .and_then(Value::as_u64)
        .unwrap_or(DEFAULT_MAX_RUNTIME_SECONDS);
    let max_runtime_seconds = input
        .get("max_allowed_runtime_seconds")
        .and_then(Value::as_u64)
        .unwrap_or(DEFAULT_MAX_RUNTIME_SECONDS)
        .max(1);
    let approval_required = boolish(input, "approval_required")
        || boolish(input, "blocked_on_approval")
        || status == "awaiting_approval";

    match operation.as_str() {
        "create" => create_decision(
            workspace_id,
            run_id,
            idempotency_key,
            prompt,
            &mode,
            &priority,
            requested_budget_cents,
            max_budget_cents,
            requested_runtime_seconds,
            max_runtime_seconds,
        ),
        "dispatch" => dispatch_decision(
            workspace_id,
            run_id,
            &status,
            &mode,
            &priority,
            approval_required,
        ),
        "retry" => retry_decision(workspace_id, run_id, &status, attempts, max_attempts),
        "cancel" => cancel_decision(workspace_id, run_id, &status),
        "complete" => terminal_decision(
            workspace_id,
            run_id,
            &status,
            "complete",
            "completed",
            "run_complete_allowed",
        ),
        "fail" => terminal_decision(
            workspace_id,
            run_id,
            &status,
            "fail",
            "failed",
            "run_fail_allowed",
        ),
        _ => protocol::block("unknown_run_operation"),
    }
}

fn create_decision(
    workspace_id: Option<String>,
    run_id: Option<String>,
    idempotency_key: Option<String>,
    prompt: Option<String>,
    mode: &str,
    priority: &str,
    requested_budget_cents: u64,
    max_budget_cents: u64,
    requested_runtime_seconds: u64,
    max_runtime_seconds: u64,
) -> Value {
    if workspace_id.is_none() {
        return protocol::block("workspace_id_missing");
    }
    let Some(prompt) = prompt else {
        return protocol::block("run_prompt_missing");
    };
    if prompt.trim().is_empty() {
        return protocol::block("run_prompt_missing");
    }
    if requested_budget_cents > max_budget_cents {
        return run_response(
            "require_approval",
            "run_budget_exceeds_limit",
            "create",
            workspace_id,
            run_id,
            "new",
            "awaiting_approval",
            "approval",
            mode,
            priority,
            idempotency_key,
            true,
            requested_budget_cents,
            max_budget_cents,
            requested_runtime_seconds.min(max_runtime_seconds),
        );
    }
    if requested_runtime_seconds > max_runtime_seconds {
        return run_response(
            "require_approval",
            "run_runtime_exceeds_limit",
            "create",
            workspace_id,
            run_id,
            "new",
            "awaiting_approval",
            "approval",
            mode,
            priority,
            idempotency_key,
            true,
            requested_budget_cents,
            max_budget_cents,
            max_runtime_seconds,
        );
    }
    run_response(
        "allow",
        "run_create_allowed",
        "create",
        workspace_id,
        run_id,
        "new",
        "queued",
        lane_for(mode, priority),
        mode,
        priority,
        idempotency_key,
        false,
        requested_budget_cents,
        max_budget_cents,
        requested_runtime_seconds,
    )
}

fn dispatch_decision(
    workspace_id: Option<String>,
    run_id: Option<String>,
    status: &str,
    mode: &str,
    priority: &str,
    approval_required: bool,
) -> Value {
    if workspace_id.is_none() {
        return protocol::block("workspace_id_missing");
    }
    if run_id.is_none() {
        return protocol::block("run_id_missing");
    }
    if approval_required {
        return run_response(
            "require_approval",
            "run_dispatch_waiting_for_approval",
            "dispatch",
            workspace_id,
            run_id,
            status,
            "awaiting_approval",
            "approval",
            mode,
            priority,
            None,
            true,
            0,
            DEFAULT_MAX_BUDGET_CENTS,
            DEFAULT_MAX_RUNTIME_SECONDS,
        );
    }
    if !matches!(status, "queued" | "approved" | "retry_scheduled") {
        return run_response(
            "block",
            "run_not_dispatchable",
            "dispatch",
            workspace_id,
            run_id,
            status,
            status,
            "none",
            mode,
            priority,
            None,
            false,
            0,
            DEFAULT_MAX_BUDGET_CENTS,
            DEFAULT_MAX_RUNTIME_SECONDS,
        );
    }
    run_response(
        "allow",
        "run_dispatch_allowed",
        "dispatch",
        workspace_id,
        run_id,
        status,
        "running",
        lane_for(mode, priority),
        mode,
        priority,
        None,
        false,
        0,
        DEFAULT_MAX_BUDGET_CENTS,
        DEFAULT_MAX_RUNTIME_SECONDS,
    )
}

fn retry_decision(
    workspace_id: Option<String>,
    run_id: Option<String>,
    status: &str,
    attempts: u64,
    max_attempts: u64,
) -> Value {
    if run_id.is_none() {
        return protocol::block("run_id_missing");
    }
    if attempts >= max_attempts {
        return run_response(
            "block",
            "run_retry_attempts_exhausted",
            "retry",
            workspace_id,
            run_id,
            status,
            status,
            "none",
            "background",
            "normal",
            None,
            false,
            0,
            DEFAULT_MAX_BUDGET_CENTS,
            DEFAULT_MAX_RUNTIME_SECONDS,
        );
    }
    if !matches!(status, "failed" | "retry_scheduled") {
        return run_response(
            "block",
            "run_not_retryable",
            "retry",
            workspace_id,
            run_id,
            status,
            status,
            "none",
            "background",
            "normal",
            None,
            false,
            0,
            DEFAULT_MAX_BUDGET_CENTS,
            DEFAULT_MAX_RUNTIME_SECONDS,
        );
    }
    run_response(
        "allow",
        "run_retry_allowed",
        "retry",
        workspace_id,
        run_id,
        status,
        "queued",
        "background",
        "background",
        "normal",
        None,
        false,
        0,
        DEFAULT_MAX_BUDGET_CENTS,
        DEFAULT_MAX_RUNTIME_SECONDS,
    )
}

fn cancel_decision(workspace_id: Option<String>, run_id: Option<String>, status: &str) -> Value {
    if run_id.is_none() {
        return protocol::block("run_id_missing");
    }
    if is_terminal(status) {
        return run_response(
            "allow",
            "run_already_terminal",
            "cancel",
            workspace_id,
            run_id,
            status,
            status,
            "none",
            "interactive",
            "normal",
            None,
            false,
            0,
            DEFAULT_MAX_BUDGET_CENTS,
            DEFAULT_MAX_RUNTIME_SECONDS,
        );
    }
    run_response(
        "allow",
        "run_cancel_allowed",
        "cancel",
        workspace_id,
        run_id,
        status,
        "cancelled",
        "none",
        "interactive",
        "normal",
        None,
        false,
        0,
        DEFAULT_MAX_BUDGET_CENTS,
        DEFAULT_MAX_RUNTIME_SECONDS,
    )
}

fn terminal_decision(
    workspace_id: Option<String>,
    run_id: Option<String>,
    status: &str,
    operation: &str,
    next_status: &str,
    reason: &str,
) -> Value {
    if run_id.is_none() {
        return protocol::block("run_id_missing");
    }
    if !matches!(status, "running" | "queued" | "approved") {
        return run_response(
            "block",
            "run_not_active",
            operation,
            workspace_id,
            run_id,
            status,
            status,
            "none",
            "background",
            "normal",
            None,
            false,
            0,
            DEFAULT_MAX_BUDGET_CENTS,
            DEFAULT_MAX_RUNTIME_SECONDS,
        );
    }
    run_response(
        "allow",
        reason,
        operation,
        workspace_id,
        run_id,
        status,
        next_status,
        "none",
        "background",
        "normal",
        None,
        false,
        0,
        DEFAULT_MAX_BUDGET_CENTS,
        DEFAULT_MAX_RUNTIME_SECONDS,
    )
}

fn run_response(
    decision: &str,
    reason: &str,
    operation: &str,
    workspace_id: Option<String>,
    run_id: Option<String>,
    current_status: &str,
    next_status: &str,
    lane: &str,
    mode: &str,
    priority: &str,
    idempotency_key: Option<String>,
    approval_required: bool,
    budget_cents: u64,
    max_budget_cents: u64,
    runtime_seconds: u64,
) -> Value {
    let next_action = if decision == "block" {
        "deny_run_operation"
    } else if decision == "require_approval" {
        "request_run_approval"
    } else {
        match operation {
            "create" => "create_run",
            "dispatch" => "dispatch_run",
            "retry" => "retry_run",
            "cancel" => "cancel_run",
            "complete" => "complete_run",
            "fail" => "fail_run",
            _ => "deny_run_operation",
        }
    };
    json!({
        "ok": decision != "block",
        "decision": decision,
        "reason": reason,
        "next_action": next_action,
        "operation": operation,
        "workspace_id": workspace_id,
        "run_id": run_id,
        "current_status": current_status,
        "next_status": next_status,
        "lane": lane,
        "mode": mode,
        "priority": priority,
        "idempotency_key": idempotency_key,
        "approval_required": approval_required || decision == "require_approval",
        "budget": {
            "requested_cents": budget_cents,
            "max_cents": max_budget_cents,
        },
        "runtime": {
            "max_runtime_seconds": runtime_seconds,
        },
        "terminal": is_terminal(next_status),
        "cacheable": false,
        "audit_visibility": if decision == "allow" && !is_terminal(next_status) { "standard" } else { "security" },
    })
}

fn lane_for(mode: &str, priority: &str) -> &'static str {
    if priority == "urgent" {
        "interactive"
    } else {
        match mode {
            "background" | "batch" => "background",
            "scheduled" => "scheduled",
            _ => "interactive",
        }
    }
}

fn is_terminal(status: &str) -> bool {
    matches!(status, "completed" | "failed" | "cancelled")
}

fn normalize_operation(value: &str) -> String {
    match normalize_token(value).as_str() {
        "create" | "new" => "create".to_string(),
        "dispatch" | "start" | "run" => "dispatch".to_string(),
        "retry" | "requeue" => "retry".to_string(),
        "cancel" | "abort" => "cancel".to_string(),
        "complete" | "succeed" | "success" => "complete".to_string(),
        "fail" | "failure" => "fail".to_string(),
        _ => String::new(),
    }
}

fn normalize_status(value: &str) -> String {
    match normalize_token(value).as_str() {
        "new" | "none" => "new".to_string(),
        "queued" | "pending" => "queued".to_string(),
        "awaiting_approval" | "approval_required" | "needs_approval" => {
            "awaiting_approval".to_string()
        }
        "approved" => "approved".to_string(),
        "running" | "active" => "running".to_string(),
        "retry_scheduled" | "retry" => "retry_scheduled".to_string(),
        "completed" | "succeeded" | "success" => "completed".to_string(),
        "failed" | "error" => "failed".to_string(),
        "cancelled" | "canceled" => "cancelled".to_string(),
        _ => "new".to_string(),
    }
}

fn normalize_mode(value: &str) -> String {
    match normalize_token(value).as_str() {
        "background" | "batch" => "background".to_string(),
        "scheduled" | "cron" => "scheduled".to_string(),
        _ => "interactive".to_string(),
    }
}

fn normalize_priority(value: &str) -> String {
    match normalize_token(value).as_str() {
        "urgent" | "high" => "urgent".to_string(),
        "low" => "low".to_string(),
        _ => "normal".to_string(),
    }
}

fn boolish(input: &Value, key: &str) -> bool {
    match input.get(key) {
        Some(Value::Bool(value)) => *value,
        Some(Value::String(value)) => matches!(
            normalize_token(value).as_str(),
            "1" | "true" | "yes" | "on" | "enabled" | "active"
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
        let response = run_orchestration_decision_command(&json!({}));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "run_operation_missing");
    }

    #[test]
    fn create_requires_workspace() {
        let response = run_orchestration_decision_command(&json!({
            "operation": "create",
            "prompt": "Do work"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "workspace_id_missing");
    }

    #[test]
    fn create_allows_valid_interactive_run() {
        let response = run_orchestration_decision_command(&json!({
            "operation": "create",
            "workspace_id": "w1",
            "prompt": "Do work",
            "mode": "interactive",
            "priority": "urgent"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_status"], "queued");
        assert_eq!(response["lane"], "interactive");
        assert_eq!(response["next_action"], "create_run");
    }

    #[test]
    fn create_over_budget_requires_approval() {
        let response = run_orchestration_decision_command(&json!({
            "operation": "create",
            "workspace_id": "w1",
            "prompt": "Do work",
            "requested_budget_cents": 2000,
            "max_budget_cents": 1000
        }));
        assert_eq!(response["decision"], "require_approval");
        assert_eq!(response["approval_required"], true);
        assert_eq!(response["next_action"], "request_run_approval");
    }

    #[test]
    fn dispatch_waits_for_approval() {
        let response = run_orchestration_decision_command(&json!({
            "operation": "dispatch",
            "workspace_id": "w1",
            "run_id": "r1",
            "status": "awaiting_approval"
        }));
        assert_eq!(response["decision"], "require_approval");
        assert_eq!(response["lane"], "approval");
        assert_eq!(response["next_action"], "request_run_approval");
    }

    #[test]
    fn retry_exhaustion_blocks() {
        let response = run_orchestration_decision_command(&json!({
            "operation": "retry",
            "run_id": "r1",
            "status": "failed",
            "attempts": 3,
            "max_attempts": 3
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "run_retry_attempts_exhausted");
        assert_eq!(response["next_action"], "deny_run_operation");
    }
}

use crate::policy::normalize_token;
use crate::protocol;
use serde_json::{json, Value};

const TERMINAL_STATES: &[&str] = &["completed", "failed", "cancelled", "archived"];

pub fn state_transition_decision_command(input: &Value) -> Value {
    let operation = normalize_operation(
        protocol::string_field(input, "operation")
            .or_else(|| protocol::string_field(input, "action"))
            .as_deref()
            .unwrap_or(""),
    );
    if operation.is_empty() {
        return protocol::block("state_operation_missing");
    }

    let entity_type = normalize_entity_type(
        protocol::string_field(input, "entity_type")
            .as_deref()
            .unwrap_or("run"),
    );
    let current_state = normalize_state(
        protocol::string_field(input, "current_state")
            .or_else(|| protocol::string_field(input, "status"))
            .as_deref()
            .unwrap_or("none"),
    );
    let entity_id = protocol::string_field(input, "entity_id")
        .or_else(|| protocol::string_field(input, "run_id"))
        .or_else(|| protocol::string_field(input, "session_id"));
    let actor_id = protocol::string_field(input, "actor_id")
        .or_else(|| protocol::string_field(input, "worker_id"))
        .or_else(|| protocol::string_field(input, "user_id"));

    if version_conflict(input) {
        return state_response(
            "block",
            "state_version_conflict",
            &operation,
            &entity_type,
            entity_id,
            actor_id,
            &current_state,
            &current_state,
            false,
            false,
        );
    }

    match operation.as_str() {
        "create" => create_decision(entity_type, entity_id, actor_id, &current_state),
        "start" => transition_if(
            entity_type,
            entity_id,
            actor_id,
            &operation,
            &current_state,
            &["queued", "retry_scheduled", "approved"],
            "running",
            "state_start_allowed",
        ),
        "require_approval" => transition_if(
            entity_type,
            entity_id,
            actor_id,
            &operation,
            &current_state,
            &["queued", "running"],
            "awaiting_approval",
            "state_approval_required",
        ),
        "approve" => transition_if(
            entity_type,
            entity_id,
            actor_id,
            &operation,
            &current_state,
            &["awaiting_approval"],
            "approved",
            "state_approval_granted",
        ),
        "deny" => transition_if(
            entity_type,
            entity_id,
            actor_id,
            &operation,
            &current_state,
            &["awaiting_approval"],
            "cancelled",
            "state_approval_denied",
        ),
        "complete" => complete_decision(entity_type, entity_id, actor_id, &current_state),
        "fail" => fail_decision(entity_type, entity_id, actor_id, &current_state),
        "retry" => retry_decision(entity_type, entity_id, actor_id, &current_state, input),
        "cancel" => cancel_decision(entity_type, entity_id, actor_id, &current_state),
        "archive" => archive_decision(entity_type, entity_id, actor_id, &current_state),
        "heartbeat" => heartbeat_decision(entity_type, entity_id, actor_id, &current_state),
        _ => protocol::block("unknown_state_operation"),
    }
}

fn create_decision(
    entity_type: String,
    entity_id: Option<String>,
    actor_id: Option<String>,
    current_state: &str,
) -> Value {
    if !matches!(current_state, "none" | "unknown") {
        return state_response(
            "block",
            "state_already_exists",
            "create",
            &entity_type,
            entity_id,
            actor_id,
            current_state,
            current_state,
            false,
            false,
        );
    }
    state_response(
        "allow",
        "state_create_allowed",
        "create",
        &entity_type,
        entity_id,
        actor_id,
        current_state,
        "queued",
        false,
        true,
    )
}

fn transition_if(
    entity_type: String,
    entity_id: Option<String>,
    actor_id: Option<String>,
    operation: &str,
    current_state: &str,
    allowed_current: &[&str],
    next_state: &str,
    reason: &str,
) -> Value {
    if !allowed_current.contains(&current_state) {
        return state_response(
            "block",
            "invalid_state_transition",
            operation,
            &entity_type,
            entity_id,
            actor_id,
            current_state,
            current_state,
            false,
            false,
        );
    }
    state_response(
        "allow",
        reason,
        operation,
        &entity_type,
        entity_id,
        actor_id,
        current_state,
        next_state,
        is_terminal(next_state),
        true,
    )
}

fn fail_decision(
    entity_type: String,
    entity_id: Option<String>,
    actor_id: Option<String>,
    current_state: &str,
) -> Value {
    if is_terminal(current_state) {
        return state_response(
            "allow",
            "state_already_terminal",
            "fail",
            &entity_type,
            entity_id,
            actor_id,
            current_state,
            current_state,
            true,
            false,
        );
    }
    transition_if(
        entity_type,
        entity_id,
        actor_id,
        "fail",
        current_state,
        &["queued", "running", "retry_scheduled", "approved"],
        "failed",
        "state_fail_allowed",
    )
}

fn complete_decision(
    entity_type: String,
    entity_id: Option<String>,
    actor_id: Option<String>,
    current_state: &str,
) -> Value {
    if is_terminal(current_state) {
        return state_response(
            "allow",
            "state_already_terminal",
            "complete",
            &entity_type,
            entity_id,
            actor_id,
            current_state,
            current_state,
            true,
            false,
        );
    }
    transition_if(
        entity_type,
        entity_id,
        actor_id,
        "complete",
        current_state,
        &["running"],
        "completed",
        "state_complete_allowed",
    )
}

fn retry_decision(
    entity_type: String,
    entity_id: Option<String>,
    actor_id: Option<String>,
    current_state: &str,
    input: &Value,
) -> Value {
    let attempts = input.get("attempts").and_then(Value::as_u64).unwrap_or(0);
    let max_attempts = input
        .get("max_attempts")
        .and_then(Value::as_u64)
        .unwrap_or(3)
        .max(1);
    if attempts >= max_attempts {
        return state_response(
            "block",
            "state_retry_attempts_exhausted",
            "retry",
            &entity_type,
            entity_id,
            actor_id,
            current_state,
            current_state,
            is_terminal(current_state),
            false,
        );
    }
    transition_if(
        entity_type,
        entity_id,
        actor_id,
        "retry",
        current_state,
        &["failed", "retry_scheduled"],
        "queued",
        "state_retry_allowed",
    )
}

fn cancel_decision(
    entity_type: String,
    entity_id: Option<String>,
    actor_id: Option<String>,
    current_state: &str,
) -> Value {
    if is_terminal(current_state) {
        return state_response(
            "allow",
            "state_already_terminal",
            "cancel",
            &entity_type,
            entity_id,
            actor_id,
            current_state,
            current_state,
            true,
            false,
        );
    }
    state_response(
        "allow",
        "state_cancel_allowed",
        "cancel",
        &entity_type,
        entity_id,
        actor_id,
        current_state,
        "cancelled",
        true,
        true,
    )
}

fn archive_decision(
    entity_type: String,
    entity_id: Option<String>,
    actor_id: Option<String>,
    current_state: &str,
) -> Value {
    if !is_terminal(current_state) || current_state == "archived" {
        return state_response(
            "block",
            "state_not_archivable",
            "archive",
            &entity_type,
            entity_id,
            actor_id,
            current_state,
            current_state,
            is_terminal(current_state),
            false,
        );
    }
    state_response(
        "allow",
        "state_archive_allowed",
        "archive",
        &entity_type,
        entity_id,
        actor_id,
        current_state,
        "archived",
        true,
        true,
    )
}

fn heartbeat_decision(
    entity_type: String,
    entity_id: Option<String>,
    actor_id: Option<String>,
    current_state: &str,
) -> Value {
    if current_state != "running" {
        return state_response(
            "block",
            "state_heartbeat_not_running",
            "heartbeat",
            &entity_type,
            entity_id,
            actor_id,
            current_state,
            current_state,
            is_terminal(current_state),
            false,
        );
    }
    state_response(
        "allow",
        "state_heartbeat_allowed",
        "heartbeat",
        &entity_type,
        entity_id,
        actor_id,
        current_state,
        current_state,
        false,
        false,
    )
}

fn state_response(
    decision: &str,
    reason: &str,
    operation: &str,
    entity_type: &str,
    entity_id: Option<String>,
    actor_id: Option<String>,
    current_state: &str,
    next_state: &str,
    terminal: bool,
    version_increment: bool,
) -> Value {
    json!({
        "ok": decision != "block",
        "decision": decision,
        "reason": reason,
        "operation": operation,
        "next_action": next_action(operation, next_state),
        "entity_type": entity_type,
        "entity_id": entity_id,
        "actor_id": actor_id,
        "current_state": current_state,
        "next_state": next_state,
        "terminal": terminal,
        "version_increment": version_increment,
        "cacheable": false,
        "audit_visibility": if decision == "allow" && !terminal { "standard" } else { "security" },
    })
}

fn next_action(operation: &str, next_state: &str) -> &'static str {
    match operation {
        "create" => "create_state_record",
        "start" => "start_state_transition",
        "require_approval" => "require_state_approval",
        "approve" => "approve_state_transition",
        "deny" => "deny_state_transition",
        "complete" => "complete_state_transition",
        "fail" if next_state == "failed" => "fail_state_transition",
        "fail" => "review_state_transition",
        "retry" => "retry_state_transition",
        "cancel" => "cancel_state_transition",
        "archive" => "archive_state_transition",
        "heartbeat" => "heartbeat_state_transition",
        _ => "review_state_transition",
    }
}

fn version_conflict(input: &Value) -> bool {
    match (
        input.get("state_version").and_then(Value::as_u64),
        input.get("expected_version").and_then(Value::as_u64),
    ) {
        (Some(current), Some(expected)) => current != expected,
        _ => false,
    }
}

fn is_terminal(state: &str) -> bool {
    TERMINAL_STATES.contains(&state)
}

fn normalize_operation(value: &str) -> String {
    match normalize_token(value).as_str() {
        "create" | "insert" => "create".to_string(),
        "start" | "run" => "start".to_string(),
        "require_approval" | "approval_required" | "wait_for_approval" => {
            "require_approval".to_string()
        }
        "approve" | "approved" => "approve".to_string(),
        "deny" | "denied" | "reject" => "deny".to_string(),
        "complete" | "succeed" | "success" => "complete".to_string(),
        "fail" | "failure" => "fail".to_string(),
        "retry" | "requeue" => "retry".to_string(),
        "cancel" | "abort" => "cancel".to_string(),
        "archive" | "retire" => "archive".to_string(),
        "heartbeat" | "touch" => "heartbeat".to_string(),
        _ => String::new(),
    }
}

fn normalize_entity_type(value: &str) -> String {
    match normalize_token(value).as_str() {
        "session" => "session".to_string(),
        "workspace" => "workspace".to_string(),
        "gateway" => "gateway".to_string(),
        _ => "run".to_string(),
    }
}

fn normalize_state(value: &str) -> String {
    match normalize_token(value).as_str() {
        "" | "none" | "missing" => "none".to_string(),
        "queued" | "pending" | "waiting" => "queued".to_string(),
        "awaiting_approval" | "needs_approval" | "approval_required" => {
            "awaiting_approval".to_string()
        }
        "approved" => "approved".to_string(),
        "running" | "active" => "running".to_string(),
        "retry_scheduled" | "retry" | "backoff" => "retry_scheduled".to_string(),
        "completed" | "succeeded" | "success" | "done" => "completed".to_string(),
        "failed" | "error" => "failed".to_string(),
        "cancelled" | "canceled" => "cancelled".to_string(),
        "archived" | "retired" => "archived".to_string(),
        _ => "unknown".to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_operation_blocks() {
        let response = state_transition_decision_command(&json!({}));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "state_operation_missing");
    }

    #[test]
    fn create_allows_missing_state() {
        let response = state_transition_decision_command(&json!({
            "operation": "create",
            "entity_id": "run-1",
            "current_state": "none"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_state"], "queued");
        assert_eq!(response["version_increment"], true);
    }

    #[test]
    fn invalid_transition_blocks() {
        let response = state_transition_decision_command(&json!({
            "operation": "complete",
            "entity_id": "run-1",
            "current_state": "queued"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "invalid_state_transition");
    }

    #[test]
    fn complete_running_run_terminal() {
        let response = state_transition_decision_command(&json!({
            "operation": "complete",
            "entity_id": "run-1",
            "current_state": "running"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_state"], "completed");
        assert_eq!(response["terminal"], true);
    }

    #[test]
    fn complete_terminal_run_is_idempotent() {
        let response = state_transition_decision_command(&json!({
            "operation": "complete",
            "entity_id": "run-1",
            "current_state": "completed"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["reason"], "state_already_terminal");
        assert_eq!(response["next_state"], "completed");
    }

    #[test]
    fn version_conflict_blocks() {
        let response = state_transition_decision_command(&json!({
            "operation": "start",
            "entity_id": "run-1",
            "current_state": "queued",
            "state_version": 3,
            "expected_version": 2
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "state_version_conflict");
    }
}

#[cfg(test)]
mod state_transition_kernel_boundary_tests {
    use super::state_transition_decision_command;

    #[test]
    fn complete_requires_running_state() {
        let decision = state_transition_decision_command(&serde_json::json!({
            "operation": "complete",
            "entity_type": "run",
            "run_id": "run-1",
            "actor_id": "worker-1",
            "current_state": "waiting_for_input"
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("block")
        );
        assert_eq!(
            decision.get("reason").and_then(|value| value.as_str()),
            Some("invalid_state_transition")
        );
    }

    #[test]
    fn fail_allows_running_run_state() {
        let decision = state_transition_decision_command(&serde_json::json!({
            "operation": "fail",
            "entity_type": "run",
            "run_id": "run-1",
            "actor_id": "worker-1",
            "current_state": "running"
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("allow")
        );
        assert_eq!(
            decision.get("next_state").and_then(|value| value.as_str()),
            Some("failed")
        );
    }

    #[test]
    fn fail_allows_terminal_run_state_idempotently() {
        let decision = state_transition_decision_command(&serde_json::json!({
            "operation": "fail",
            "entity_type": "run",
            "run_id": "run-1",
            "actor_id": "worker-1",
            "current_state": "failed"
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("allow")
        );
        assert_eq!(
            decision.get("reason").and_then(|value| value.as_str()),
            Some("state_already_terminal")
        );
    }
}

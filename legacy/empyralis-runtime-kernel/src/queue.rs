use crate::policy::normalize_token;
use crate::protocol;
use serde_json::{json, Value};

const DEFAULT_MAX_ATTEMPTS: u64 = 3;
const DEFAULT_RETRY_DELAY_SECONDS: u64 = 30;
const MAX_RETRY_DELAY_SECONDS: u64 = 960;

pub fn queue_transition_decision_command(input: &Value) -> Value {
    let operation = normalize_operation(
        protocol::string_field(input, "operation")
            .or_else(|| protocol::string_field(input, "action"))
            .as_deref()
            .unwrap_or(""),
    );
    if operation.is_empty() {
        return protocol::block("queue_operation_missing");
    }

    let item_id = protocol::string_field(input, "item_id")
        .or_else(|| protocol::string_field(input, "run_id"))
        .or_else(|| protocol::string_field(input, "queue_id"));
    let requester_id = protocol::string_field(input, "requester_id")
        .or_else(|| protocol::string_field(input, "worker_id"))
        .or_else(|| protocol::string_field(input, "holder_id"));
    let lease_holder_id = protocol::string_field(input, "lease_holder_id")
        .or_else(|| protocol::string_field(input, "current_holder_id"));
    let status = normalize_status(
        protocol::string_field(input, "status")
            .or_else(|| protocol::string_field(input, "current_status"))
            .as_deref()
            .unwrap_or("unknown"),
    );
    let attempts = input.get("attempts").and_then(Value::as_u64).unwrap_or(0);
    let max_attempts = input
        .get("max_attempts")
        .and_then(Value::as_u64)
        .unwrap_or(DEFAULT_MAX_ATTEMPTS)
        .max(1);
    let stale = boolish(input, "stale") || boolish(input, "lease_stale");
    let force = boolish(input, "force");

    match operation.as_str() {
        "enqueue" => enqueue_decision(item_id, &status),
        "claim" => claim_decision(
            item_id,
            requester_id,
            lease_holder_id,
            &status,
            stale,
            force,
        ),
        "complete" => complete_decision(item_id, requester_id, lease_holder_id, &status),
        "fail" => fail_decision(
            item_id,
            requester_id,
            lease_holder_id,
            &status,
            attempts,
            max_attempts,
        ),
        "retry" => retry_decision(item_id, &status, attempts, max_attempts),
        "cancel" => cancel_decision(item_id, requester_id, lease_holder_id, &status, force),
        "release" => release_decision(item_id, requester_id, lease_holder_id, &status, stale),
        "dead_letter" => {
            dead_letter_decision(item_id, &status, protocol::string_field(input, "reason"))
        }
        _ => protocol::block("unknown_queue_operation"),
    }
}

fn enqueue_decision(item_id: Option<String>, status: &str) -> Value {
    let Some(item_id) = item_id else {
        return protocol::block("queue_item_missing");
    };
    if matches!(status, "running" | "completed" | "failed" | "cancelled") {
        return queue_response(
            "block",
            "queue_item_not_enqueueable",
            "enqueue",
            Some(item_id),
            None,
            None,
            status,
            status,
            false,
            0,
            false,
        );
    }
    queue_response(
        "allow",
        "queue_enqueue_allowed",
        "enqueue",
        Some(item_id),
        None,
        None,
        status,
        "queued",
        false,
        0,
        false,
    )
}

fn claim_decision(
    item_id: Option<String>,
    requester_id: Option<String>,
    lease_holder_id: Option<String>,
    status: &str,
    stale: bool,
    force: bool,
) -> Value {
    let Some(item_id) = item_id else {
        return protocol::block("queue_item_missing");
    };
    let Some(requester_id) = requester_id else {
        return protocol::block("queue_requester_missing");
    };
    if matches!(status, "running" | "claimed")
        && lease_holder_id.as_deref() != Some(requester_id.as_str())
        && !stale
        && !force
    {
        return queue_response(
            "block",
            "queue_item_already_claimed",
            "claim",
            Some(item_id),
            Some(requester_id),
            lease_holder_id,
            status,
            status,
            false,
            0,
            false,
        );
    }
    if matches!(status, "completed" | "failed" | "cancelled") {
        return queue_response(
            "block",
            "queue_terminal_item_not_claimable",
            "claim",
            Some(item_id),
            Some(requester_id),
            lease_holder_id,
            status,
            status,
            false,
            0,
            false,
        );
    }
    queue_response(
        if force && lease_holder_id.is_some() {
            "require_approval"
        } else {
            "allow"
        },
        if stale {
            "stale_queue_claim_allowed"
        } else if force && lease_holder_id.is_some() {
            "forced_queue_claim_requires_approval"
        } else {
            "queue_claim_allowed"
        },
        "claim",
        Some(item_id),
        Some(requester_id),
        lease_holder_id,
        status,
        "running",
        false,
        0,
        false,
    )
}

fn terminal_decision(
    operation: &str,
    next_status: &str,
    reason: &str,
    item_id: Option<String>,
    requester_id: Option<String>,
    lease_holder_id: Option<String>,
    status: &str,
    force: bool,
) -> Value {
    let Some(item_id) = item_id else {
        return protocol::block("queue_item_missing");
    };
    if !matches!(status, "running" | "claimed") {
        return queue_response(
            "block",
            "queue_item_not_active",
            operation,
            Some(item_id),
            requester_id,
            lease_holder_id,
            status,
            status,
            false,
            0,
            false,
        );
    }
    if let (Some(requester), Some(holder)) = (requester_id.as_ref(), lease_holder_id.as_ref()) {
        if requester != holder && !force {
            return queue_response(
                "block",
                "queue_item_not_owned_by_requester",
                operation,
                Some(item_id),
                requester_id,
                lease_holder_id,
                status,
                status,
                false,
                0,
                false,
            );
        }
    }
    queue_response(
        "allow",
        reason,
        operation,
        Some(item_id),
        requester_id,
        lease_holder_id,
        status,
        next_status,
        false,
        0,
        true,
    )
}

fn complete_decision(
    item_id: Option<String>,
    requester_id: Option<String>,
    lease_holder_id: Option<String>,
    status: &str,
) -> Value {
    if matches!(status, "completed" | "failed" | "cancelled") {
        return queue_response(
            "allow",
            "queue_item_already_terminal",
            "complete",
            item_id,
            requester_id,
            lease_holder_id,
            status,
            status,
            false,
            0,
            true,
        );
    }
    terminal_decision(
        "complete",
        "completed",
        "queue_complete_allowed",
        item_id,
        requester_id,
        lease_holder_id,
        status,
        false,
    )
}

fn fail_decision(
    item_id: Option<String>,
    requester_id: Option<String>,
    lease_holder_id: Option<String>,
    status: &str,
    attempts: u64,
    max_attempts: u64,
) -> Value {
    let Some(item_id) = item_id else {
        return protocol::block("queue_item_missing");
    };
    if matches!(status, "completed" | "failed" | "cancelled") {
        return queue_response(
            "allow",
            "queue_item_already_terminal",
            "fail",
            Some(item_id),
            requester_id,
            lease_holder_id,
            status,
            status,
            false,
            0,
            true,
        );
    }
    if !matches!(status, "running" | "claimed") {
        return queue_response(
            "block",
            "queue_item_not_active",
            "fail",
            Some(item_id),
            requester_id,
            lease_holder_id,
            status,
            status,
            false,
            0,
            false,
        );
    }
    let next_attempts = attempts.saturating_add(1);
    if next_attempts < max_attempts {
        let delay = retry_delay_seconds(next_attempts);
        return queue_response(
            "allow",
            "queue_failure_retry_scheduled",
            "fail",
            Some(item_id),
            requester_id,
            lease_holder_id,
            status,
            "retry_scheduled",
            true,
            delay,
            false,
        );
    }
    queue_response(
        "allow",
        "queue_failure_terminal",
        "fail",
        Some(item_id),
        requester_id,
        lease_holder_id,
        status,
        "failed",
        false,
        0,
        true,
    )
}

fn retry_decision(
    item_id: Option<String>,
    status: &str,
    attempts: u64,
    max_attempts: u64,
) -> Value {
    let Some(item_id) = item_id else {
        return protocol::block("queue_item_missing");
    };
    if attempts >= max_attempts {
        return queue_response(
            "block",
            "queue_retry_attempts_exhausted",
            "retry",
            Some(item_id),
            None,
            None,
            status,
            status,
            false,
            0,
            false,
        );
    }
    if !matches!(status, "retry_scheduled" | "failed" | "queued") {
        return queue_response(
            "block",
            "queue_item_not_retryable",
            "retry",
            Some(item_id),
            None,
            None,
            status,
            status,
            false,
            0,
            false,
        );
    }
    queue_response(
        "allow",
        "queue_retry_allowed",
        "retry",
        Some(item_id),
        None,
        None,
        status,
        "queued",
        false,
        0,
        false,
    )
}

fn cancel_decision(
    item_id: Option<String>,
    requester_id: Option<String>,
    lease_holder_id: Option<String>,
    status: &str,
    force: bool,
) -> Value {
    if status == "queued" {
        let Some(item_id) = item_id else {
            return protocol::block("queue_item_missing");
        };
        return queue_response(
            "allow",
            if force {
                "queue_forced_cancel_allowed"
            } else {
                "queue_cancel_allowed"
            },
            "cancel",
            Some(item_id),
            requester_id,
            lease_holder_id,
            status,
            "cancelled",
            false,
            0,
            true,
        );
    }
    if matches!(status, "completed" | "failed" | "cancelled") {
        return queue_response(
            "allow",
            "queue_item_already_terminal",
            "cancel",
            item_id,
            requester_id,
            lease_holder_id,
            status,
            status,
            false,
            0,
            true,
        );
    }
    terminal_decision(
        "cancel",
        "cancelled",
        if force {
            "queue_forced_cancel_allowed"
        } else {
            "queue_cancel_allowed"
        },
        item_id,
        requester_id,
        lease_holder_id,
        status,
        force,
    )
}

fn release_decision(
    item_id: Option<String>,
    requester_id: Option<String>,
    lease_holder_id: Option<String>,
    status: &str,
    stale: bool,
) -> Value {
    let Some(item_id) = item_id else {
        return protocol::block("queue_item_missing");
    };
    if !matches!(status, "running" | "claimed") {
        return queue_response(
            "block",
            "queue_item_not_releasable",
            "release",
            Some(item_id),
            requester_id,
            lease_holder_id,
            status,
            status,
            false,
            0,
            false,
        );
    }
    if let (Some(requester), Some(holder)) = (requester_id.as_ref(), lease_holder_id.as_ref()) {
        if requester != holder && !stale {
            return queue_response(
                "block",
                "queue_release_not_owned",
                "release",
                Some(item_id),
                requester_id,
                lease_holder_id,
                status,
                status,
                false,
                0,
                false,
            );
        }
    }
    queue_response(
        "allow",
        if stale {
            "stale_queue_release_allowed"
        } else {
            "queue_release_allowed"
        },
        "release",
        Some(item_id),
        requester_id,
        lease_holder_id,
        status,
        "queued",
        false,
        0,
        false,
    )
}

fn dead_letter_decision(
    item_id: Option<String>,
    status: &str,
    reason_text: Option<String>,
) -> Value {
    let Some(item_id) = item_id else {
        return protocol::block("queue_item_missing");
    };
    let reason = reason_text
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty());
    if reason.is_none() {
        return queue_response(
            "block",
            "queue_dead_letter_reason_missing",
            "dead_letter",
            Some(item_id),
            None,
            None,
            status,
            status,
            false,
            0,
            false,
        );
    }
    if matches!(status, "completed" | "cancelled") {
        return queue_response(
            "block",
            "queue_dead_letter_invalid_status",
            "dead_letter",
            Some(item_id),
            None,
            None,
            status,
            status,
            false,
            0,
            false,
        );
    }
    queue_response(
        "allow",
        "queue_dead_letter_allowed",
        "dead_letter",
        Some(item_id),
        None,
        None,
        status,
        "failed",
        false,
        0,
        true,
    )
}

fn queue_response(
    decision: &str,
    reason: &str,
    operation: &str,
    item_id: Option<String>,
    requester_id: Option<String>,
    lease_holder_id: Option<String>,
    current_status: &str,
    next_status: &str,
    retryable: bool,
    retry_delay_seconds: u64,
    terminal: bool,
) -> Value {
    json!({
        "ok": decision != "block",
        "decision": decision,
        "reason": reason,
        "operation": operation,
        "next_action": next_action(operation, next_status, retryable),
        "item_id": item_id,
        "requester_id": requester_id,
        "lease_holder_id": lease_holder_id,
        "current_status": current_status,
        "next_status": next_status,
        "retryable": retryable,
        "retry_delay_seconds": retry_delay_seconds,
        "terminal": terminal,
        "approval_required": decision == "require_approval",
        "cacheable": false,
        "audit_visibility": if decision == "allow" && !terminal { "standard" } else { "security" },
    })
}

fn next_action(operation: &str, next_status: &str, retryable: bool) -> &'static str {
    match operation {
        "enqueue" => "enqueue_queue_item",
        "claim" => "claim_queue_item",
        "complete" => "complete_queue_item",
        "fail" if retryable || next_status == "retry_scheduled" => "schedule_queue_retry",
        "fail" => "fail_queue_item",
        "retry" => "retry_queue_item",
        "cancel" => "cancel_queue_item",
        "release" => "release_queue_item",
        "dead_letter" => "dead_letter_queue_item",
        _ => "review_queue_transition",
    }
}

fn retry_delay_seconds(attempts: u64) -> u64 {
    let multiplier = 2_u64.saturating_pow(attempts.min(5) as u32);
    DEFAULT_RETRY_DELAY_SECONDS
        .saturating_mul(multiplier)
        .min(MAX_RETRY_DELAY_SECONDS)
}

fn normalize_operation(value: &str) -> String {
    match normalize_token(value).as_str() {
        "enqueue" | "queue" | "create" => "enqueue".to_string(),
        "claim" | "lease" | "start" => "claim".to_string(),
        "complete" | "succeed" | "success" => "complete".to_string(),
        "fail" | "failure" => "fail".to_string(),
        "retry" | "requeue" => "retry".to_string(),
        "cancel" | "abort" => "cancel".to_string(),
        "release" | "unlock" => "release".to_string(),
        "dead_letter" | "deadletter" | "poison" => "dead_letter".to_string(),
        _ => String::new(),
    }
}

fn normalize_status(value: &str) -> String {
    match normalize_token(value).as_str() {
        "queued" | "queued_local" | "pending" | "waiting" | "waiting_for_input" | "starting" => {
            "queued".to_string()
        }
        "claimed" | "leased" | "running_local" => "claimed".to_string(),
        "running" | "active" => "running".to_string(),
        "retry_scheduled" | "retry" | "backoff" => "retry_scheduled".to_string(),
        "completed" | "succeeded" | "success" | "done" => "completed".to_string(),
        "failed" | "error" => "failed".to_string(),
        "cancelled" | "canceled" => "cancelled".to_string(),
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
        let response = queue_transition_decision_command(&json!({}));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "queue_operation_missing");
    }

    #[test]
    fn claim_allows_queued_item() {
        let response = queue_transition_decision_command(&json!({
            "operation": "claim",
            "item_id": "run-1",
            "requester_id": "worker-1",
            "status": "queued"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_action"], "claim_queue_item");
        assert_eq!(response["next_status"], "running");
    }

    #[test]
    fn claim_blocks_other_active_holder() {
        let response = queue_transition_decision_command(&json!({
            "operation": "claim",
            "item_id": "run-1",
            "requester_id": "worker-2",
            "lease_holder_id": "worker-1",
            "status": "running"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "queue_item_already_claimed");
    }

    #[test]
    fn fail_schedules_retry_before_attempts_exhausted() {
        let response = queue_transition_decision_command(&json!({
            "operation": "fail",
            "item_id": "run-1",
            "status": "running",
            "attempts": 0,
            "max_attempts": 3
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_action"], "schedule_queue_retry");
        assert_eq!(response["next_status"], "retry_scheduled");
        assert_eq!(response["retryable"], true);
    }

    #[test]
    fn fail_terminal_after_attempts_exhausted() {
        let response = queue_transition_decision_command(&json!({
            "operation": "fail",
            "item_id": "run-1",
            "status": "running",
            "attempts": 2,
            "max_attempts": 3
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_status"], "failed");
        assert_eq!(response["terminal"], true);
    }

    #[test]
    fn complete_terminal_item_is_idempotent() {
        let response = queue_transition_decision_command(&json!({
            "operation": "complete",
            "item_id": "run-1",
            "status": "completed"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["reason"], "queue_item_already_terminal");
    }

    #[test]
    fn fail_terminal_item_is_idempotent() {
        let response = queue_transition_decision_command(&json!({
            "operation": "fail",
            "item_id": "run-1",
            "status": "failed",
            "attempts": 3,
            "max_attempts": 3
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["reason"], "queue_item_already_terminal");
    }

    #[test]
    fn cancel_allows_queued_item() {
        let response = queue_transition_decision_command(&json!({
            "operation": "cancel",
            "item_id": "run-1",
            "requester_id": "operator",
            "status": "queued_local"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_status"], "cancelled");
        assert_eq!(response["terminal"], true);
    }

    #[test]
    fn dead_letter_requires_reason() {
        let response = queue_transition_decision_command(&json!({
            "operation": "dead_letter",
            "item_id": "run-1",
            "status": "failed"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "queue_dead_letter_reason_missing");
    }

    #[test]
    fn dead_letter_allows_failed_item() {
        let response = queue_transition_decision_command(&json!({
            "operation": "dead_letter",
            "item_id": "run-1",
            "status": "failed",
            "reason": "worker_lost_retry_exhausted"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["reason"], "queue_dead_letter_allowed");
        assert_eq!(response["next_status"], "failed");
        assert_eq!(response["terminal"], true);
    }
}

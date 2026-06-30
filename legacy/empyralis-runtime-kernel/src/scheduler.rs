use crate::policy::normalize_token;
use crate::protocol;
use serde_json::{json, Value};

const DEFAULT_MAX_RETRIES: u64 = 5;
const DEFAULT_BASE_BACKOFF_SECONDS: u64 = 30;
const DEFAULT_MAX_BACKOFF_SECONDS: u64 = 960;
const DEFAULT_MAX_EVENT_TRIGGERS_PER_HOUR: u64 = 12;
const DEFAULT_MAX_SELF_PROPOSED_PER_HOUR: u64 = 4;

pub fn scheduler_decision_command(input: &Value) -> Value {
    let operation = normalize_operation(
        protocol::string_field(input, "operation")
            .or_else(|| protocol::string_field(input, "action"))
            .as_deref()
            .unwrap_or(""),
    );
    if operation.is_empty() {
        return protocol::block("scheduler_operation_missing");
    }

    let enabled = !boolish(input, "disabled") && boolish_default(input, "enabled", true);
    let quiet_hours_start = input
        .get("quiet_hours_start")
        .and_then(Value::as_u64)
        .unwrap_or(22)
        .min(23);
    let quiet_hours_end = input
        .get("quiet_hours_end")
        .and_then(Value::as_u64)
        .unwrap_or(7)
        .min(23);
    let current_hour = input
        .get("current_hour")
        .and_then(Value::as_u64)
        .unwrap_or(12)
        .min(23);
    let quiet_override = boolish(input, "quiet_hours_override") || boolish(input, "owner_override");
    let event_triggers_this_hour = input
        .get("event_triggers_this_hour")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let self_proposed_this_hour = input
        .get("self_proposed_this_hour")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let max_event_triggers_per_hour = input
        .get("max_event_triggers_per_hour")
        .and_then(Value::as_u64)
        .unwrap_or(DEFAULT_MAX_EVENT_TRIGGERS_PER_HOUR)
        .max(1);
    let max_self_proposed_per_hour = input
        .get("max_self_proposed_per_hour")
        .and_then(Value::as_u64)
        .unwrap_or(DEFAULT_MAX_SELF_PROPOSED_PER_HOUR)
        .max(1);
    let retry_count = input
        .get("retry_count")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let max_retries = input
        .get("max_retries")
        .and_then(Value::as_u64)
        .unwrap_or(DEFAULT_MAX_RETRIES)
        .max(1);
    let wake_mode = normalize_wake_mode(
        protocol::string_field(input, "wake_mode")
            .as_deref()
            .unwrap_or("now"),
    );

    if !enabled && operation != "status" {
        return scheduler_response(
            "block",
            "scheduler_disabled",
            &operation,
            "none",
            0,
            retry_count,
            max_retries,
            current_hour,
            quiet_hours_start,
            quiet_hours_end,
            false,
        );
    }

    let quiet_now = in_quiet_hours(current_hour, quiet_hours_start, quiet_hours_end);
    match operation.as_str() {
        "status" => scheduler_response(
            "allow",
            "scheduler_status_ready",
            "status",
            if quiet_now { "quiet" } else { "active" },
            0,
            retry_count,
            max_retries,
            current_hour,
            quiet_hours_start,
            quiet_hours_end,
            quiet_now,
        ),
        "wake" | "trigger" => wake_decision(
            &operation,
            &wake_mode,
            quiet_now,
            quiet_override,
            event_triggers_this_hour,
            max_event_triggers_per_hour,
            self_proposed_this_hour,
            max_self_proposed_per_hour,
            retry_count,
            max_retries,
            current_hour,
            quiet_hours_start,
            quiet_hours_end,
        ),
        "retry" => retry_decision(
            retry_count,
            max_retries,
            current_hour,
            quiet_hours_start,
            quiet_hours_end,
            quiet_now,
            quiet_override,
        ),
        "fail" => fail_decision(
            retry_count,
            max_retries,
            current_hour,
            quiet_hours_start,
            quiet_hours_end,
        ),
        "defer" => scheduler_response(
            "defer",
            "scheduler_defer_allowed",
            "defer",
            "defer",
            retry_delay_seconds(retry_count),
            retry_count,
            max_retries,
            current_hour,
            quiet_hours_start,
            quiet_hours_end,
            quiet_now,
        ),
        _ => protocol::block("unknown_scheduler_operation"),
    }
}

fn wake_decision(
    operation: &str,
    wake_mode: &str,
    quiet_now: bool,
    quiet_override: bool,
    event_triggers_this_hour: u64,
    max_event_triggers_per_hour: u64,
    self_proposed_this_hour: u64,
    max_self_proposed_per_hour: u64,
    retry_count: u64,
    max_retries: u64,
    current_hour: u64,
    quiet_hours_start: u64,
    quiet_hours_end: u64,
) -> Value {
    if quiet_now && !quiet_override && wake_mode != "owner_approved" {
        return scheduler_response(
            "defer",
            "quiet_hours_defer",
            operation,
            "defer",
            seconds_until_quiet_end(current_hour, quiet_hours_end),
            retry_count,
            max_retries,
            current_hour,
            quiet_hours_start,
            quiet_hours_end,
            quiet_now,
        );
    }
    if event_triggers_this_hour >= max_event_triggers_per_hour {
        return scheduler_response(
            "defer",
            "event_trigger_rate_limited",
            operation,
            "defer",
            3600,
            retry_count,
            max_retries,
            current_hour,
            quiet_hours_start,
            quiet_hours_end,
            quiet_now,
        );
    }
    if wake_mode == "self_proposed" && self_proposed_this_hour >= max_self_proposed_per_hour {
        return scheduler_response(
            "require_approval",
            "self_proposed_wake_rate_limited",
            operation,
            "request_owner_approval",
            0,
            retry_count,
            max_retries,
            current_hour,
            quiet_hours_start,
            quiet_hours_end,
            quiet_now,
        );
    }
    scheduler_response(
        "allow",
        "scheduler_wake_allowed",
        operation,
        "wake",
        0,
        retry_count,
        max_retries,
        current_hour,
        quiet_hours_start,
        quiet_hours_end,
        quiet_now,
    )
}

fn retry_decision(
    retry_count: u64,
    max_retries: u64,
    current_hour: u64,
    quiet_hours_start: u64,
    quiet_hours_end: u64,
    quiet_now: bool,
    quiet_override: bool,
) -> Value {
    if retry_count >= max_retries {
        return scheduler_response(
            "block",
            "retry_attempts_exhausted",
            "retry",
            "permanent_fail",
            0,
            retry_count,
            max_retries,
            current_hour,
            quiet_hours_start,
            quiet_hours_end,
            quiet_now,
        );
    }
    if quiet_now && !quiet_override {
        return scheduler_response(
            "defer",
            "retry_deferred_by_quiet_hours",
            "retry",
            "defer",
            seconds_until_quiet_end(current_hour, quiet_hours_end),
            retry_count,
            max_retries,
            current_hour,
            quiet_hours_start,
            quiet_hours_end,
            quiet_now,
        );
    }
    scheduler_response(
        "allow",
        "retry_scheduled",
        "retry",
        "retry",
        retry_delay_seconds(retry_count),
        retry_count,
        max_retries,
        current_hour,
        quiet_hours_start,
        quiet_hours_end,
        quiet_now,
    )
}

fn fail_decision(
    retry_count: u64,
    max_retries: u64,
    current_hour: u64,
    quiet_hours_start: u64,
    quiet_hours_end: u64,
) -> Value {
    scheduler_response(
        "allow",
        if retry_count >= max_retries {
            "scheduler_permanent_failure_allowed"
        } else {
            "scheduler_failure_recorded"
        },
        "fail",
        if retry_count >= max_retries {
            "permanent_fail"
        } else {
            "record_failure"
        },
        0,
        retry_count,
        max_retries,
        current_hour,
        quiet_hours_start,
        quiet_hours_end,
        in_quiet_hours(current_hour, quiet_hours_start, quiet_hours_end),
    )
}

fn scheduler_response(
    decision: &str,
    reason: &str,
    operation: &str,
    next_action: &str,
    delay_seconds: u64,
    retry_count: u64,
    max_retries: u64,
    current_hour: u64,
    quiet_hours_start: u64,
    quiet_hours_end: u64,
    quiet_now: bool,
) -> Value {
    json!({
        "ok": decision != "block",
        "decision": decision,
        "reason": reason,
        "operation": operation,
        "next_action": next_action,
        "delay_seconds": delay_seconds,
        "retry_count": retry_count,
        "max_retries": max_retries,
        "quiet_hours": {
            "active": quiet_now,
            "start_hour": quiet_hours_start,
            "end_hour": quiet_hours_end,
            "current_hour": current_hour,
        },
        "approval_required": decision == "require_approval",
        "cacheable": false,
        "audit_visibility": if decision == "allow" { "standard" } else { "security" },
    })
}

fn retry_delay_seconds(retry_count: u64) -> u64 {
    DEFAULT_BASE_BACKOFF_SECONDS
        .saturating_mul(2_u64.saturating_pow(retry_count.min(5) as u32))
        .min(DEFAULT_MAX_BACKOFF_SECONDS)
}

fn seconds_until_quiet_end(current_hour: u64, quiet_hours_end: u64) -> u64 {
    let hours = if current_hour < quiet_hours_end {
        quiet_hours_end - current_hour
    } else {
        24 - current_hour + quiet_hours_end
    };
    hours.max(1) * 3600
}

fn in_quiet_hours(current_hour: u64, start: u64, end: u64) -> bool {
    if start == end {
        return false;
    }
    if start < end {
        current_hour >= start && current_hour < end
    } else {
        current_hour >= start || current_hour < end
    }
}

fn normalize_operation(value: &str) -> String {
    match normalize_token(value).as_str() {
        "status" | "snapshot" => "status".to_string(),
        "wake" | "run" => "wake".to_string(),
        "trigger" | "event" => "trigger".to_string(),
        "retry" | "backoff" => "retry".to_string(),
        "fail" | "failure" => "fail".to_string(),
        "defer" | "delay" => "defer".to_string(),
        _ => String::new(),
    }
}

fn normalize_wake_mode(value: &str) -> String {
    match normalize_token(value).as_str() {
        "self_proposed" | "self" => "self_proposed".to_string(),
        "owner_approved" | "approved" => "owner_approved".to_string(),
        "event" | "trigger" => "event".to_string(),
        _ => "now".to_string(),
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

fn boolish_default(input: &Value, key: &str, default_value: bool) -> bool {
    if input.get(key).is_none() {
        return default_value;
    }
    boolish(input, key)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_operation_blocks() {
        let response = scheduler_decision_command(&json!({}));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "scheduler_operation_missing");
    }

    #[test]
    fn wake_defers_during_quiet_hours() {
        let response = scheduler_decision_command(&json!({
            "operation": "wake",
            "current_hour": 23,
            "quiet_hours_start": 22,
            "quiet_hours_end": 7
        }));
        assert_eq!(response["decision"], "defer");
        assert_eq!(response["reason"], "quiet_hours_defer");
    }

    #[test]
    fn owner_override_allows_quiet_hours_wake() {
        let response = scheduler_decision_command(&json!({
            "operation": "wake",
            "current_hour": 23,
            "quiet_hours_start": 22,
            "quiet_hours_end": 7,
            "owner_override": true
        }));
        assert_eq!(response["decision"], "allow");
    }

    #[test]
    fn retry_exhaustion_blocks() {
        let response = scheduler_decision_command(&json!({
            "operation": "retry",
            "retry_count": 5,
            "max_retries": 5
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["next_action"], "permanent_fail");
    }

    #[test]
    fn self_proposed_rate_limit_requires_approval() {
        let response = scheduler_decision_command(&json!({
            "operation": "wake",
            "wake_mode": "self_proposed",
            "self_proposed_this_hour": 4,
            "max_self_proposed_per_hour": 4
        }));
        assert_eq!(response["decision"], "require_approval");
        assert_eq!(response["approval_required"], true);
    }
}

#[cfg(test)]
mod scheduler_kernel_boundary_tests {
    use super::scheduler_decision_command;

    #[test]
    fn retry_blocks_when_attempts_exhausted() {
        let decision = scheduler_decision_command(&serde_json::json!({
            "operation": "retry",
            "enabled": true,
            "retry_count": 3,
            "max_retries": 3,
            "current_hour": 12,
            "quiet_hours_override": true
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("block")
        );
        assert_eq!(
            decision.get("reason").and_then(|value| value.as_str()),
            Some("retry_attempts_exhausted")
        );
    }

    #[test]
    fn retry_returns_backoff_delay_when_allowed() {
        let decision = scheduler_decision_command(&serde_json::json!({
            "operation": "retry",
            "enabled": true,
            "retry_count": 1,
            "max_retries": 3,
            "current_hour": 12,
            "quiet_hours_override": true
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("allow")
        );
        assert_eq!(
            decision.get("next_action").and_then(|value| value.as_str()),
            Some("retry")
        );
        assert!(
            decision
                .get("delay_seconds")
                .and_then(|value| value.as_u64())
                .unwrap_or(0)
                > 0
        );
    }
}

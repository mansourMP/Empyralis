use crate::policy::normalize_token;
use crate::protocol;
use serde_json::{json, Value};

pub fn heartbeat_snapshot_command(input: &Value) -> Value {
    let workspace_id = protocol::string_field(input, "workspace_id");
    let tenant_id = protocol::string_field(input, "tenant_id");
    let queue = input.get("queue").unwrap_or(input);
    let scheduler = input.get("scheduler").unwrap_or(input);
    let session = input.get("session").unwrap_or(input);

    let queued_count = u64_field(queue, "queued_count");
    let running_now_count = u64_field(queue, "running_now_count");
    let blocked_on_approval_count = u64_field(queue, "blocked_on_approval_count");
    let failed_count = u64_field(queue, "failed_count");
    let retry_pending_count = u64_field(scheduler, "retry_pending_count")
        .max(u64_field(scheduler, "pending_retry_count"));
    let quiet_hours_active = boolish(scheduler, "quiet_hours_active");
    let scheduler_enabled = boolish_default(scheduler, "enabled", true);
    let rust_kernel_available = boolish_default(input, "rust_kernel_available", true);
    let session_status = normalize_status(
        protocol::string_field(session, "status")
            .as_deref()
            .unwrap_or("active"),
    );
    let session_turn_count = u64_field(session, "turn_count");
    let session_max_turns = optional_u64(session, "max_turns");
    let idle_seconds = u64_field(session, "idle_seconds");

    let health = health_label(
        rust_kernel_available,
        scheduler_enabled,
        failed_count,
        blocked_on_approval_count,
        quiet_hours_active,
        &session_status,
    );
    let next_action = next_action(
        &health,
        queued_count,
        running_now_count,
        blocked_on_approval_count,
        retry_pending_count,
        quiet_hours_active,
        &session_status,
    );

    json!({
        "ok": true,
        "decision": "allow",
        "reason": "heartbeat_snapshot_ready",
        "workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "health": health,
        "next_action": next_action,
        "runtime": {
            "rust_kernel_available": rust_kernel_available,
            "scheduler_enabled": scheduler_enabled,
            "quiet_hours_active": quiet_hours_active,
        },
        "queue": {
            "queued_count": queued_count,
            "running_now_count": running_now_count,
            "blocked_on_approval_count": blocked_on_approval_count,
            "failed_count": failed_count,
            "retry_pending_count": retry_pending_count,
        },
        "session": {
            "status": session_status,
            "turn_count": session_turn_count,
            "max_turns": session_max_turns,
            "idle_seconds": idle_seconds,
            "turns_remaining": turns_remaining(session_turn_count, session_max_turns),
        },
        "audit_visibility": if health == "healthy" { "standard" } else { "security" },
        "cacheable": false,
    })
}

fn health_label(
    rust_kernel_available: bool,
    scheduler_enabled: bool,
    failed_count: u64,
    blocked_on_approval_count: u64,
    quiet_hours_active: bool,
    session_status: &str,
) -> &'static str {
    if !rust_kernel_available || session_status == "closed" {
        "critical"
    } else if !scheduler_enabled || failed_count > 0 {
        "degraded"
    } else if blocked_on_approval_count > 0 {
        "needs_approval"
    } else if quiet_hours_active {
        "quiet"
    } else {
        "healthy"
    }
}

fn next_action(
    health: &str,
    queued_count: u64,
    running_now_count: u64,
    blocked_on_approval_count: u64,
    retry_pending_count: u64,
    quiet_hours_active: bool,
    session_status: &str,
) -> &'static str {
    if health == "critical" {
        "investigate"
    } else if blocked_on_approval_count > 0 {
        "request_owner_approval"
    } else if session_status == "reset_required" {
        "reset_session"
    } else if quiet_hours_active {
        "wait_for_quiet_hours"
    } else if running_now_count > 0 {
        "monitor_running_work"
    } else if retry_pending_count > 0 {
        "wait_for_retry"
    } else if queued_count > 0 {
        "claim_next_work"
    } else {
        "idle"
    }
}

fn turns_remaining(turn_count: u64, max_turns: Option<u64>) -> Option<u64> {
    max_turns.map(|limit| limit.saturating_sub(turn_count))
}

fn normalize_status(value: &str) -> String {
    match normalize_token(value).as_str() {
        "active" | "open" | "running" => "active".to_string(),
        "reset_required" => "reset_required".to_string(),
        "prune_eligible" => "prune_eligible".to_string(),
        "closed" | "ended" => "closed".to_string(),
        _ => "active".to_string(),
    }
}

fn u64_field(input: &Value, key: &str) -> u64 {
    input.get(key).and_then(Value::as_u64).unwrap_or(0)
}

fn optional_u64(input: &Value, key: &str) -> Option<u64> {
    input.get(key).and_then(Value::as_u64)
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
    fn healthy_idle_snapshot() {
        let response = heartbeat_snapshot_command(&json!({
            "workspace_id": "w1",
            "queue": {"queued_count": 0, "running_now_count": 0},
            "scheduler": {"enabled": true},
            "session": {"status": "active", "turn_count": 3, "max_turns": 10}
        }));
        assert_eq!(response["health"], "healthy");
        assert_eq!(response["next_action"], "idle");
        assert_eq!(response["session"]["turns_remaining"], 7);
    }

    #[test]
    fn approval_queue_needs_approval() {
        let response = heartbeat_snapshot_command(&json!({
            "queue": {"blocked_on_approval_count": 2},
            "scheduler": {"enabled": true},
            "session": {"status": "active"}
        }));
        assert_eq!(response["health"], "needs_approval");
        assert_eq!(response["next_action"], "request_owner_approval");
    }

    #[test]
    fn missing_rust_kernel_is_critical() {
        let response = heartbeat_snapshot_command(&json!({
            "rust_kernel_available": false,
            "queue": {},
            "scheduler": {"enabled": true},
            "session": {"status": "active"}
        }));
        assert_eq!(response["health"], "critical");
        assert_eq!(response["next_action"], "investigate");
    }

    #[test]
    fn quiet_hours_waits() {
        let response = heartbeat_snapshot_command(&json!({
            "queue": {"queued_count": 4},
            "scheduler": {"quiet_hours_active": true},
            "session": {"status": "active"}
        }));
        assert_eq!(response["health"], "quiet");
        assert_eq!(response["next_action"], "wait_for_quiet_hours");
    }
}

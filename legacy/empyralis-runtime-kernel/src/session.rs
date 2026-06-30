use crate::policy::normalize_token;
use crate::protocol;
use serde_json::{json, Value};

const SHORT_MAX_TURNS: u64 = 20;
const STANDARD_MAX_TURNS: u64 = 80;
const EXTENDED_MAX_TURNS: u64 = 200;
const SHORT_MAX_HOURS: u64 = 4;
const STANDARD_MAX_HOURS: u64 = 24;
const EXTENDED_MAX_HOURS: u64 = 168;
const DEFAULT_IDLE_SECONDS: u64 = 3600;

#[derive(Clone, Copy)]
struct SessionBounds {
    max_turns: Option<u64>,
    max_age_hours: Option<u64>,
    idle_timeout_seconds: Option<u64>,
}

pub fn session_lifecycle_decision_command(input: &Value) -> Value {
    let operation = normalize_operation(
        protocol::string_field(input, "operation")
            .or_else(|| protocol::string_field(input, "action"))
            .as_deref()
            .unwrap_or(""),
    );
    if operation.is_empty() {
        return protocol::block("session_operation_missing");
    }

    let session_id = protocol::string_field(input, "session_id");
    let status = normalize_status(
        protocol::string_field(input, "status")
            .or_else(|| protocol::string_field(input, "current_status"))
            .as_deref()
            .unwrap_or("active"),
    );
    let preset = normalize_preset(
        protocol::string_field(input, "preset")
            .or_else(|| protocol::string_field(input, "lifecycle_preset"))
            .as_deref()
            .unwrap_or("standard"),
    );
    let bounds = bounds_for(&preset, input);
    let turn_count = input.get("turn_count").and_then(Value::as_u64).unwrap_or(0);
    let age_hours = input.get("age_hours").and_then(Value::as_u64).unwrap_or(0);
    let idle_seconds = input
        .get("idle_seconds")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let force = boolish(input, "force");

    if status == "closed" && !matches!(operation.as_str(), "create" | "prune") {
        return lifecycle_response(
            "block",
            "session_already_closed",
            &operation,
            session_id,
            &preset,
            &status,
            "closed",
            "none",
            turn_count,
            age_hours,
            idle_seconds,
            bounds,
        );
    }

    match operation.as_str() {
        "create" => create_decision(session_id, &preset, &status, bounds),
        "turn" => turn_decision(
            session_id,
            &preset,
            &status,
            turn_count,
            age_hours,
            idle_seconds,
            bounds,
        ),
        "idle_check" => idle_check_decision(
            session_id,
            &preset,
            &status,
            turn_count,
            age_hours,
            idle_seconds,
            bounds,
        ),
        "age_check" => age_check_decision(
            session_id,
            &preset,
            &status,
            turn_count,
            age_hours,
            idle_seconds,
            bounds,
        ),
        "reset" => reset_decision(
            session_id,
            &preset,
            &status,
            turn_count,
            age_hours,
            idle_seconds,
            bounds,
            force,
        ),
        "extend" => extend_decision(
            session_id,
            &preset,
            &status,
            turn_count,
            age_hours,
            idle_seconds,
            bounds,
        ),
        "close" => close_decision(
            session_id,
            &preset,
            &status,
            turn_count,
            age_hours,
            idle_seconds,
            bounds,
        ),
        "prune" => prune_decision(
            session_id,
            &preset,
            &status,
            turn_count,
            age_hours,
            idle_seconds,
            bounds,
            force,
        ),
        _ => protocol::block("unknown_session_operation"),
    }
}

fn create_decision(
    session_id: Option<String>,
    preset: &str,
    status: &str,
    bounds: SessionBounds,
) -> Value {
    if status != "new" && session_id.is_some() {
        return lifecycle_response(
            "block",
            "session_already_exists",
            "create",
            session_id,
            preset,
            status,
            status,
            "none",
            0,
            0,
            0,
            bounds,
        );
    }
    lifecycle_response(
        "allow",
        "session_create_allowed",
        "create",
        session_id,
        preset,
        status,
        "active",
        "create",
        0,
        0,
        0,
        bounds,
    )
}

fn turn_decision(
    session_id: Option<String>,
    preset: &str,
    status: &str,
    turn_count: u64,
    age_hours: u64,
    idle_seconds: u64,
    bounds: SessionBounds,
) -> Value {
    if let Some(max_turns) = bounds.max_turns {
        if turn_count.saturating_add(1) > max_turns {
            return lifecycle_response(
                "require_approval",
                "session_turn_limit_reached",
                "turn",
                session_id,
                preset,
                status,
                "reset_required",
                "reset",
                turn_count,
                age_hours,
                idle_seconds,
                bounds,
            );
        }
    }
    lifecycle_response(
        "allow",
        "session_turn_allowed",
        "turn",
        session_id,
        preset,
        status,
        "active",
        "continue",
        turn_count.saturating_add(1),
        age_hours,
        0,
        bounds,
    )
}

fn idle_check_decision(
    session_id: Option<String>,
    preset: &str,
    status: &str,
    turn_count: u64,
    age_hours: u64,
    idle_seconds: u64,
    bounds: SessionBounds,
) -> Value {
    if let Some(timeout) = bounds.idle_timeout_seconds {
        if idle_seconds >= timeout {
            return lifecycle_response(
                "allow",
                "session_idle_prune_allowed",
                "idle_check",
                session_id,
                preset,
                status,
                "prune_eligible",
                "prune",
                turn_count,
                age_hours,
                idle_seconds,
                bounds,
            );
        }
    }
    lifecycle_response(
        "allow",
        "session_idle_within_limit",
        "idle_check",
        session_id,
        preset,
        status,
        status,
        "continue",
        turn_count,
        age_hours,
        idle_seconds,
        bounds,
    )
}

fn age_check_decision(
    session_id: Option<String>,
    preset: &str,
    status: &str,
    turn_count: u64,
    age_hours: u64,
    idle_seconds: u64,
    bounds: SessionBounds,
) -> Value {
    if let Some(max_age) = bounds.max_age_hours {
        if age_hours >= max_age {
            return lifecycle_response(
                "allow",
                "session_age_reset_allowed",
                "age_check",
                session_id,
                preset,
                status,
                "reset_required",
                "reset",
                turn_count,
                age_hours,
                idle_seconds,
                bounds,
            );
        }
    }
    lifecycle_response(
        "allow",
        "session_age_within_limit",
        "age_check",
        session_id,
        preset,
        status,
        status,
        "continue",
        turn_count,
        age_hours,
        idle_seconds,
        bounds,
    )
}

fn reset_decision(
    session_id: Option<String>,
    preset: &str,
    status: &str,
    turn_count: u64,
    age_hours: u64,
    idle_seconds: u64,
    bounds: SessionBounds,
    force: bool,
) -> Value {
    lifecycle_response(
        if force { "require_approval" } else { "allow" },
        if force {
            "forced_session_reset_requires_approval"
        } else {
            "session_reset_allowed"
        },
        "reset",
        session_id,
        preset,
        status,
        "active",
        "reset",
        turn_count,
        age_hours,
        idle_seconds,
        bounds,
    )
}

fn extend_decision(
    session_id: Option<String>,
    preset: &str,
    status: &str,
    turn_count: u64,
    age_hours: u64,
    idle_seconds: u64,
    bounds: SessionBounds,
) -> Value {
    lifecycle_response(
        if preset == "unlimited" {
            "allow"
        } else {
            "require_approval"
        },
        if preset == "unlimited" {
            "session_already_unlimited"
        } else {
            "session_extension_requires_approval"
        },
        "extend",
        session_id,
        preset,
        status,
        status,
        "extend",
        turn_count,
        age_hours,
        idle_seconds,
        bounds,
    )
}

fn close_decision(
    session_id: Option<String>,
    preset: &str,
    status: &str,
    turn_count: u64,
    age_hours: u64,
    idle_seconds: u64,
    bounds: SessionBounds,
) -> Value {
    lifecycle_response(
        "allow",
        "session_close_allowed",
        "close",
        session_id,
        preset,
        status,
        "closed",
        "close",
        turn_count,
        age_hours,
        idle_seconds,
        bounds,
    )
}

fn prune_decision(
    session_id: Option<String>,
    preset: &str,
    status: &str,
    turn_count: u64,
    age_hours: u64,
    idle_seconds: u64,
    bounds: SessionBounds,
    force: bool,
) -> Value {
    let idle_eligible = bounds
        .idle_timeout_seconds
        .map(|timeout| idle_seconds >= timeout)
        .unwrap_or(false);
    let age_eligible = bounds
        .max_age_hours
        .map(|max_age| age_hours >= max_age)
        .unwrap_or(false);
    if !force && status != "prune_eligible" && !idle_eligible && !age_eligible {
        return lifecycle_response(
            "block",
            "session_not_prune_eligible",
            "prune",
            session_id,
            preset,
            status,
            status,
            "continue",
            turn_count,
            age_hours,
            idle_seconds,
            bounds,
        );
    }
    lifecycle_response(
        "allow",
        "session_prune_allowed",
        "prune",
        session_id,
        preset,
        status,
        "closed",
        "prune",
        turn_count,
        age_hours,
        idle_seconds,
        bounds,
    )
}

fn lifecycle_response(
    decision: &str,
    reason: &str,
    operation: &str,
    session_id: Option<String>,
    preset: &str,
    current_status: &str,
    next_status: &str,
    next_action: &str,
    turn_count: u64,
    age_hours: u64,
    idle_seconds: u64,
    bounds: SessionBounds,
) -> Value {
    json!({
        "ok": decision != "block",
        "decision": decision,
        "reason": reason,
        "operation": operation,
        "session_id": session_id,
        "preset": preset,
        "current_status": current_status,
        "next_status": next_status,
        "next_action": next_action,
        "turn_count": turn_count,
        "age_hours": age_hours,
        "idle_seconds": idle_seconds,
        "limits": {
            "max_turns": bounds.max_turns,
            "max_age_hours": bounds.max_age_hours,
            "idle_timeout_seconds": bounds.idle_timeout_seconds,
        },
        "approval_required": decision == "require_approval",
        "cacheable": false,
        "audit_visibility": if decision == "allow" && next_action == "continue" { "standard" } else { "security" },
    })
}

fn bounds_for(preset: &str, input: &Value) -> SessionBounds {
    let mut bounds = match preset {
        "short" => SessionBounds {
            max_turns: Some(SHORT_MAX_TURNS),
            max_age_hours: Some(SHORT_MAX_HOURS),
            idle_timeout_seconds: Some(DEFAULT_IDLE_SECONDS),
        },
        "extended" => SessionBounds {
            max_turns: Some(EXTENDED_MAX_TURNS),
            max_age_hours: Some(EXTENDED_MAX_HOURS),
            idle_timeout_seconds: Some(DEFAULT_IDLE_SECONDS * 4),
        },
        "unlimited" => SessionBounds {
            max_turns: None,
            max_age_hours: None,
            idle_timeout_seconds: None,
        },
        _ => SessionBounds {
            max_turns: Some(STANDARD_MAX_TURNS),
            max_age_hours: Some(STANDARD_MAX_HOURS),
            idle_timeout_seconds: Some(DEFAULT_IDLE_SECONDS * 2),
        },
    };
    if let Some(value) = input.get("max_turns").and_then(Value::as_u64) {
        bounds.max_turns = Some(value.max(1));
    }
    if let Some(value) = input.get("max_age_hours").and_then(Value::as_u64) {
        bounds.max_age_hours = Some(value.max(1));
    }
    if let Some(value) = input.get("idle_timeout_seconds").and_then(Value::as_u64) {
        bounds.idle_timeout_seconds = Some(value.max(1));
    }
    bounds
}

fn normalize_operation(value: &str) -> String {
    match normalize_token(value).as_str() {
        "create" | "open" => "create".to_string(),
        "turn" | "message" | "increment" => "turn".to_string(),
        "idle_check" | "idle" => "idle_check".to_string(),
        "age_check" | "age" => "age_check".to_string(),
        "reset" | "restart" => "reset".to_string(),
        "extend" | "extension" => "extend".to_string(),
        "close" | "end" => "close".to_string(),
        "prune" | "cleanup" => "prune".to_string(),
        _ => String::new(),
    }
}

fn normalize_status(value: &str) -> String {
    match normalize_token(value).as_str() {
        "new" | "none" => "new".to_string(),
        "active" | "open" | "running" => "active".to_string(),
        "reset_required" => "reset_required".to_string(),
        "prune_eligible" => "prune_eligible".to_string(),
        "closed" | "ended" => "closed".to_string(),
        _ => "active".to_string(),
    }
}

fn normalize_preset(value: &str) -> String {
    match normalize_token(value).as_str() {
        "short" => "short".to_string(),
        "extended" | "long" => "extended".to_string(),
        "unlimited" | "infinite" => "unlimited".to_string(),
        _ => "standard".to_string(),
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
        let response = session_lifecycle_decision_command(&json!({}));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "session_operation_missing");
    }

    #[test]
    fn turn_limit_requires_reset() {
        let response = session_lifecycle_decision_command(&json!({
            "operation": "turn",
            "session_id": "s1",
            "preset": "short",
            "turn_count": SHORT_MAX_TURNS
        }));
        assert_eq!(response["decision"], "require_approval");
        assert_eq!(response["next_action"], "reset");
    }

    #[test]
    fn idle_check_marks_prune_eligible() {
        let response = session_lifecycle_decision_command(&json!({
            "operation": "idle_check",
            "session_id": "s1",
            "preset": "standard",
            "idle_seconds": DEFAULT_IDLE_SECONDS * 2
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_action"], "prune");
    }

    #[test]
    fn unlimited_turn_does_not_hit_limit() {
        let response = session_lifecycle_decision_command(&json!({
            "operation": "turn",
            "session_id": "s1",
            "preset": "unlimited",
            "turn_count": 1_000_000
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_action"], "continue");
    }

    #[test]
    fn closed_session_blocks_turn() {
        let response = session_lifecycle_decision_command(&json!({
            "operation": "turn",
            "session_id": "s1",
            "status": "closed"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "session_already_closed");
    }

    #[test]
    fn prune_blocks_active_session_within_limits() {
        let response = session_lifecycle_decision_command(&json!({
            "operation": "prune",
            "session_id": "s1",
            "status": "active",
            "idle_seconds": 1,
            "age_hours": 1,
            "idle_timeout_seconds": 3600,
            "max_age_hours": 24
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "session_not_prune_eligible");
    }

    #[test]
    fn prune_allows_idle_session() {
        let response = session_lifecycle_decision_command(&json!({
            "operation": "prune",
            "session_id": "s1",
            "status": "active",
            "idle_seconds": 3600,
            "age_hours": 1,
            "idle_timeout_seconds": 3600,
            "max_age_hours": 24
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_action"], "prune");
    }
}

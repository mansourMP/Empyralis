use serde_json::{json, Value};

const OPERATIONS: &[&str] = &[
    "resolve_lifecycle_policy",
    "session_status",
    "session_turn",
    "auto_reset_check",
    "idle_check",
    "prune_candidates",
    "prune_sessions",
    "quiet_hours_status",
    "wake_decision",
    "event_trigger",
    "self_proposed_trigger",
    "retry_metadata",
    "retry_decision",
    "schedule_retry",
    "failure_decision",
    "claim_wake_requests",
    "finalize_wake_requests",
    "ambient_monitor_status",
];

const PRESETS: &[&str] = &["short", "standard", "extended", "unlimited"];

pub fn session_scheduler_decision_command(payload: &Value) -> Value {
    let operation = normalize_operation(&text(payload, &["operation", "action"]));
    let workspace_id = text(payload, &["workspace_id"]);
    let session_id = text(payload, &["session_id"]);
    let trigger_id = text(payload, &["trigger_id", "schedule_id", "event_id"]);
    let preset = normalize_preset(&text(payload, &["preset", "lifecycle_preset"]));
    let plan_tier = fallback(normalize(&text(payload, &["plan_tier"])), "free");
    let status = fallback(
        normalize(&text(payload, &["status", "session_status"])),
        "active",
    );
    let turn_count = number(payload, &["turn_count"], 0);
    let max_turns = number(
        payload,
        &["max_turns_per_session", "max_turns"],
        preset_max_turns(&preset),
    );
    let age_hours = number(payload, &["age_hours"], 0);
    let idle_hours = number(payload, &["idle_hours"], 0);
    let idle_timeout_hours = number(payload, &["idle_timeout_hours"], preset_idle_hours(&preset));
    let prune_after_idle_days = number(
        payload,
        &["prune_after_idle_days"],
        preset_prune_days(&preset),
    );
    let retention_days = number(payload, &["retention_days"], preset_retention_days(&preset));
    let auto_reset_enabled = flag(payload, &["auto_reset_enabled"], preset != "unlimited");
    let auto_reset_after_turns = number(payload, &["auto_reset_after_turns"], 0);
    let auto_reset_after_hours = number(payload, &["auto_reset_after_hours"], 24);
    let enforce_max_turns = flag(payload, &["enforce_max_turns"], preset != "unlimited");
    let idle_pruning_enabled = flag(payload, &["idle_pruning_enabled"], preset != "unlimited");
    let dry_run = flag(payload, &["dry_run"], false);
    let quiet_hours_start = number(payload, &["quiet_hours_start"], 23).clamp(0, 23);
    let quiet_hours_end = number(payload, &["quiet_hours_end"], 7).clamp(0, 23);
    let current_hour = number(payload, &["current_hour"], 12).clamp(0, 23);
    let quiet_hours_override = flag(payload, &["quiet_hours_override", "owner_override"], false);
    let wake_mode = fallback(
        normalize(&text(payload, &["wake_mode", "trigger_kind"])),
        "event",
    );
    let priority = number(payload, &["priority"], 0);
    let event_triggers_this_hour = number(payload, &["event_triggers_this_hour"], 0);
    let self_proposed_this_hour = number(payload, &["self_proposed_this_hour"], 0);
    let max_event_triggers_per_hour = number(payload, &["max_event_triggers_per_hour"], 4).max(1);
    let max_self_proposed_per_hour = number(payload, &["max_self_proposed_per_hour"], 2).max(1);
    let max_runtime_seconds = number(payload, &["max_runtime_seconds"], 20).clamp(5, 300);
    let battery_percent = number(payload, &["battery_percent"], 100);
    let minimum_battery_percent = number(payload, &["minimum_battery_percent"], 20).clamp(0, 100);
    let network_online = flag(payload, &["network_online"], true);
    let require_network_online = flag(payload, &["require_network_online"], false);
    let owner_approval_required = flag(
        payload,
        &["require_owner_approval_for_privileged_wakeups"],
        true,
    );
    let owner_approval_provided = flag(
        payload,
        &["owner_approval_provided", "approval_provided"],
        false,
    );
    let attempt = number(payload, &["attempt", "retry_attempt"], 0).max(0);
    let max_retries = number(payload, &["max_retries", "retry_max"], 5).max(0);
    let base_delay_seconds = number(payload, &["base_delay_seconds"], 30).max(1);
    let max_delay_seconds = number(payload, &["max_delay_seconds"], 3600).max(1);
    let scheduler_enabled = flag(payload, &["scheduler_enabled", "enabled"], true);
    let ambient_registered = flag(
        payload,
        &["ambient_registered", "monitor_registered"],
        false,
    );
    let candidate_count = number(payload, &["candidate_count", "sessions_count"], 0);
    let max_prune_batch = number(payload, &["max_prune_batch"], 100).max(1);

    let quiet_now = in_quiet_hours(current_hour, quiet_hours_start, quiet_hours_end);
    let retry_delay = retry_delay_seconds(attempt, base_delay_seconds, max_delay_seconds);
    let mut blocks: Vec<String> = Vec::new();
    let mut approvals: Vec<String> = Vec::new();
    let mut normalized_flags: Vec<&str> = Vec::new();

    if operation.is_empty() {
        blocks.push("session_scheduler_operation_missing".to_string());
    } else if !OPERATIONS.contains(&operation.as_str()) {
        blocks.push(format!("unknown_session_scheduler_operation:{operation}"));
    }
    if !PRESETS.contains(&preset.as_str()) {
        blocks.push("unknown_lifecycle_preset".to_string());
    }
    if requires_workspace(&operation) && workspace_id.is_empty() {
        blocks.push("workspace_id_missing".to_string());
    }
    if requires_session(&operation) && session_id.is_empty() {
        blocks.push("session_id_missing".to_string());
    }
    if !scheduler_enabled
        && scheduler_operation(&operation)
        && operation != "ambient_monitor_status"
    {
        blocks.push("scheduler_disabled".to_string());
    }
    if retention_days <= 0 {
        blocks.push("retention_days_must_be_positive".to_string());
    } else if retention_days > 730 {
        approvals.push("extended_session_retention_requires_review".to_string());
    }
    if max_turns < 0 {
        blocks.push("max_turns_must_not_be_negative".to_string());
    }
    if enforce_max_turns
        && max_turns > 0
        && turn_count >= max_turns
        && matches!(operation.as_str(), "session_turn" | "session_status")
    {
        approvals.push("session_turn_limit_reached".to_string());
    }
    if auto_reset_enabled {
        normalized_flags.push("auto_reset_enabled");
        if auto_reset_after_turns > 0 && turn_count >= auto_reset_after_turns {
            normalized_flags.push("auto_reset_turn_threshold_met");
        }
        if auto_reset_after_hours > 0 && age_hours >= auto_reset_after_hours {
            normalized_flags.push("auto_reset_age_threshold_met");
        }
    }
    if idle_pruning_enabled {
        normalized_flags.push("idle_pruning_enabled");
        if idle_hours >= idle_timeout_hours || age_hours >= prune_after_idle_days.saturating_mul(24)
        {
            normalized_flags.push("session_prune_eligible");
        }
    }
    if matches!(operation.as_str(), "prune_sessions" | "prune_candidates") {
        if candidate_count > max_prune_batch {
            approvals.push("large_session_prune_batch_requires_review".to_string());
        }
        if !dry_run && candidate_count > 0 {
            approvals.push("session_prune_requires_approval".to_string());
        }
    }
    if scheduler_operation(&operation) {
        if quiet_now {
            normalized_flags.push("quiet_hours_active");
        }
        if battery_percent < minimum_battery_percent {
            normalized_flags.push("battery_low");
        }
        if require_network_online && !network_online {
            normalized_flags.push("network_offline");
        }
    }
    if matches!(
        operation.as_str(),
        "wake_decision" | "event_trigger" | "self_proposed_trigger"
    ) {
        if trigger_id.is_empty() {
            blocks.push("trigger_id_missing".to_string());
        }
        if quiet_now && !quiet_hours_override && wake_mode != "owner_approved" {
            normalized_flags.push("wake_deferred_by_quiet_hours");
        }
        if battery_percent < minimum_battery_percent {
            normalized_flags.push("wake_deferred_by_battery");
        }
        if require_network_online && !network_online {
            normalized_flags.push("wake_deferred_by_network");
        }
        if event_triggers_this_hour >= max_event_triggers_per_hour {
            normalized_flags.push("event_trigger_rate_limited");
        }
        if wake_mode == "self_proposed" || operation == "self_proposed_trigger" {
            if self_proposed_this_hour >= max_self_proposed_per_hour {
                approvals.push("self_proposed_wake_rate_limited".to_string());
            }
            if owner_approval_required && !owner_approval_provided && priority < 80 {
                approvals.push("self_proposed_wake_requires_owner_approval".to_string());
            }
        }
    }
    if matches!(
        operation.as_str(),
        "retry_metadata" | "retry_decision" | "schedule_retry"
    ) {
        if attempt >= max_retries {
            blocks.push("retry_attempts_exhausted".to_string());
        }
        if quiet_now && !quiet_hours_override {
            normalized_flags.push("retry_deferred_by_quiet_hours");
        }
    }
    if operation == "failure_decision" && attempt >= max_retries {
        normalized_flags.push("permanent_failure");
    }
    if operation == "ambient_monitor_status" && !ambient_registered {
        normalized_flags.push("ambient_monitor_missing");
    }

    blocks.sort();
    blocks.dedup();
    approvals.sort();
    approvals.dedup();
    normalized_flags.sort();
    normalized_flags.dedup();

    let deferred = normalized_flags.iter().any(|flag| {
        flag.starts_with("wake_deferred")
            || *flag == "event_trigger_rate_limited"
            || *flag == "retry_deferred_by_quiet_hours"
    });
    let decision = if !blocks.is_empty() {
        "block"
    } else if !approvals.is_empty() && !owner_approval_provided {
        "require_approval"
    } else if deferred {
        "defer"
    } else {
        "allow"
    };
    let reason = if !blocks.is_empty() {
        blocks.join("; ")
    } else if decision == "require_approval" {
        approvals.join("; ")
    } else if decision == "defer" {
        "session_scheduler_deferred".to_string()
    } else {
        "session_scheduler_policy_satisfied".to_string()
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
        "session_policy": {
            "preset": preset,
            "session_id": session_id,
            "status": status,
            "turn_count": turn_count,
            "max_turns_per_session": max_turns,
            "turns_remaining": turns_remaining(turn_count, max_turns, enforce_max_turns),
            "age_hours": age_hours,
            "idle_hours": idle_hours,
            "idle_timeout_hours": idle_timeout_hours,
            "prune_after_idle_days": prune_after_idle_days,
            "retention_days": retention_days,
            "auto_reset_enabled": auto_reset_enabled,
            "auto_reset_after_turns": auto_reset_after_turns,
            "auto_reset_after_hours": auto_reset_after_hours,
            "enforce_max_turns": enforce_max_turns,
            "idle_pruning_enabled": idle_pruning_enabled,
        },
        "scheduler_policy": {
            "workspace_id": workspace_id,
            "plan_tier": plan_tier,
            "quiet_hours_start": quiet_hours_start,
            "quiet_hours_end": quiet_hours_end,
            "current_hour": current_hour,
            "quiet_hours_active": quiet_now,
            "event_triggers_this_hour": event_triggers_this_hour,
            "max_event_triggers_per_hour": max_event_triggers_per_hour,
            "self_proposed_this_hour": self_proposed_this_hour,
            "max_self_proposed_per_hour": max_self_proposed_per_hour,
            "max_runtime_seconds": max_runtime_seconds,
            "battery_percent": battery_percent,
            "minimum_battery_percent": minimum_battery_percent,
            "network_online": network_online,
            "require_network_online": require_network_online,
        },
        "retry": {
            "attempt": attempt,
            "max_retries": max_retries,
            "retry_delay_seconds": retry_delay,
            "retry_next_action": if attempt >= max_retries { "permanent_fail" } else { "retry" },
        },
        "pruning": {
            "dry_run": dry_run,
            "candidate_count": candidate_count,
            "max_prune_batch": max_prune_batch,
        },
        "normalized_flags": normalized_flags,
        "block_reasons": blocks,
        "approval_reasons": approvals,
    })
}

fn text(payload: &Value, keys: &[&str]) -> String {
    for key in keys {
        if let Some(Value::String(value)) = payload.get(*key) {
            let trimmed = value.trim();
            if !trimmed.is_empty() {
                return trimmed.to_string();
            }
        }
    }
    String::new()
}

fn number(payload: &Value, keys: &[&str], default: i64) -> i64 {
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

fn flag(payload: &Value, keys: &[&str], default: bool) -> bool {
    for key in keys {
        if let Some(value) = payload.get(*key) {
            if let Some(flag) = value.as_bool() {
                return flag;
            }
            if let Some(text) = value.as_str() {
                return matches!(
                    normalize(text).as_str(),
                    "1" | "true" | "yes" | "on" | "enabled"
                );
            }
            if let Some(number) = value.as_i64() {
                return number > 0;
            }
        }
    }
    default
}

fn normalize(value: &str) -> String {
    value
        .trim()
        .to_ascii_lowercase()
        .replace('-', "_")
        .replace(' ', "_")
}

fn fallback(value: String, fallback: &str) -> String {
    if value.is_empty() {
        fallback.to_string()
    } else {
        value
    }
}

fn normalize_operation(value: &str) -> String {
    match normalize(value).as_str() {
        "policy" | "resolve_policy" | "resolve_lifecycle_policy" => {
            "resolve_lifecycle_policy".to_string()
        }
        "status" | "session_status" => "session_status".to_string(),
        "turn" | "session_turn" => "session_turn".to_string(),
        "auto_reset" | "auto_reset_check" => "auto_reset_check".to_string(),
        "idle" | "idle_check" => "idle_check".to_string(),
        "prune_candidates" => "prune_candidates".to_string(),
        "prune" | "prune_sessions" => "prune_sessions".to_string(),
        "quiet_hours" | "quiet_hours_status" => "quiet_hours_status".to_string(),
        "wake" | "wake_decision" => "wake_decision".to_string(),
        "event" | "event_trigger" => "event_trigger".to_string(),
        "self_proposed" | "self_proposed_trigger" => "self_proposed_trigger".to_string(),
        "retry_metadata" => "retry_metadata".to_string(),
        "retry" | "retry_decision" => "retry_decision".to_string(),
        "schedule_retry" => "schedule_retry".to_string(),
        "fail" | "failure_decision" => "failure_decision".to_string(),
        "claim_wake_requests" | "claim_due_wake_requests" => "claim_wake_requests".to_string(),
        "finalize_wake_requests" | "finalize_wake_request" => "finalize_wake_requests".to_string(),
        "ambient" | "ambient_monitor_status" => "ambient_monitor_status".to_string(),
        "" => String::new(),
        other => other.to_string(),
    }
}

fn normalize_preset(value: &str) -> String {
    match normalize(value).as_str() {
        "short" => "short".to_string(),
        "extended" | "long" => "extended".to_string(),
        "unlimited" | "none" => "unlimited".to_string(),
        "" | "standard" => "standard".to_string(),
        other => other.to_string(),
    }
}

fn preset_max_turns(preset: &str) -> i64 {
    match preset {
        "short" => 50,
        "extended" => 500,
        "unlimited" => 0,
        _ => 100,
    }
}

fn preset_idle_hours(preset: &str) -> i64 {
    match preset {
        "short" => 12,
        "extended" => 72,
        "unlimited" => 24,
        _ => 24,
    }
}

fn preset_prune_days(preset: &str) -> i64 {
    match preset {
        "short" => 3,
        "extended" => 30,
        "unlimited" => 7,
        _ => 7,
    }
}

fn preset_retention_days(preset: &str) -> i64 {
    match preset {
        "short" => 30,
        "extended" | "unlimited" => 730,
        _ => 365,
    }
}

fn requires_workspace(operation: &str) -> bool {
    matches!(
        operation,
        "prune_candidates"
            | "prune_sessions"
            | "quiet_hours_status"
            | "wake_decision"
            | "event_trigger"
            | "self_proposed_trigger"
            | "claim_wake_requests"
            | "finalize_wake_requests"
            | "ambient_monitor_status"
    )
}

fn requires_session(operation: &str) -> bool {
    matches!(
        operation,
        "session_status" | "session_turn" | "auto_reset_check" | "idle_check"
    )
}

fn scheduler_operation(operation: &str) -> bool {
    matches!(
        operation,
        "quiet_hours_status"
            | "wake_decision"
            | "event_trigger"
            | "self_proposed_trigger"
            | "retry_metadata"
            | "retry_decision"
            | "schedule_retry"
            | "failure_decision"
            | "claim_wake_requests"
            | "finalize_wake_requests"
            | "ambient_monitor_status"
    )
}

fn in_quiet_hours(current_hour: i64, start: i64, end: i64) -> bool {
    if start == end {
        return false;
    }
    if start < end {
        current_hour >= start && current_hour < end
    } else {
        current_hour >= start || current_hour < end
    }
}

fn retry_delay_seconds(attempt: i64, base_delay: i64, max_delay: i64) -> i64 {
    let mut delay = base_delay.max(1);
    for _ in 0..attempt.clamp(0, 16) {
        delay = delay.saturating_mul(2).min(max_delay);
    }
    delay.min(max_delay).max(1)
}

fn turns_remaining(turn_count: i64, max_turns: i64, enforce: bool) -> Option<i64> {
    if !enforce || max_turns <= 0 {
        None
    } else {
        Some((max_turns - turn_count).max(0))
    }
}

fn next_action(operation: &str, decision: &str) -> &'static str {
    if decision == "block" {
        return "stop_session_scheduler_operation";
    }
    if decision == "require_approval" {
        return "request_session_scheduler_approval";
    }
    if decision == "defer" {
        return "defer_session_scheduler_operation";
    }
    match operation {
        "resolve_lifecycle_policy" => "apply_lifecycle_policy",
        "session_status" => "return_session_status",
        "session_turn" => "allow_session_turn",
        "auto_reset_check" => "evaluate_auto_reset",
        "idle_check" => "evaluate_idle_state",
        "prune_candidates" => "list_prune_candidates",
        "prune_sessions" => "prune_idle_sessions",
        "quiet_hours_status" => "return_quiet_hours_status",
        "wake_decision" => "trigger_wakeup",
        "event_trigger" => "schedule_event_trigger",
        "self_proposed_trigger" => "schedule_self_proposed_trigger",
        "retry_metadata" => "build_retry_metadata",
        "retry_decision" => "schedule_retry",
        "schedule_retry" => "schedule_retry",
        "failure_decision" => "record_scheduler_failure",
        "claim_wake_requests" => "claim_due_wake_requests",
        "finalize_wake_requests" => "finalize_wake_requests",
        "ambient_monitor_status" => "return_ambient_monitor_status",
        _ => "review_session_scheduler_operation",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn claim_wake_requests_requires_workspace() {
        let response = session_scheduler_decision_command(&json!({
            "operation": "claim_wake_requests",
            "scheduler_enabled": true
        }));
        assert_eq!(response["decision"], "block");
        assert!(response["reason"]
            .as_str()
            .unwrap_or("")
            .contains("workspace_id_missing"));
    }

    #[test]
    fn schedule_retry_blocks_exhausted_attempts() {
        let response = session_scheduler_decision_command(&json!({
            "operation": "schedule_retry",
            "workspace_id": "workspace-1",
            "attempt": 5,
            "max_retries": 5
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["next_action"], "stop_session_scheduler_operation");
    }
}

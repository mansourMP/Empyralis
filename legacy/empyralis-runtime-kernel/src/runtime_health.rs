use serde_json::{json, Value};

const OPERATIONS: &[&str] = &[
    "heartbeat_snapshot",
    "readiness_gate",
    "queue_overview",
    "operator_next_action",
    "plugin_health",
    "rust_kernel_health",
    "runtime_lane_health",
];

pub fn runtime_health_decision_command(payload: &Value) -> Value {
    let operation = normalize_operation(&text(payload, &["operation", "action"]));
    let workspace_id = text(payload, &["workspace_id"]);
    let tenant_id = fallback(text(payload, &["tenant_id"]), "default");
    let profile_complete = bool_path(payload, &["bootstrap", "complete"], true)
        && bool_path(payload, &["profile", "complete"], true);
    let scheduler_enabled = bool_path(payload, &["scheduler", "enabled"], true)
        && bool_path(payload, &["policy", "scheduler_enabled"], true);
    let quiet_hours_active = bool_path(payload, &["quiet_hours", "active"], false)
        || bool_path(payload, &["queue_overview", "quiet_hours", "active"], false);
    let rust_kernel_available = bool_path(payload, &["rust_kernel", "available"], true)
        && bool_path(payload, &["runtime", "rust_kernel_available"], true);
    let plugin_error = text_path(payload, &["plugin", "error"]);
    let plugin_ok = plugin_error.is_empty() && bool_path(payload, &["plugin", "ok"], true);
    let ambient_registered = bool_path(payload, &["ambient_monitor", "registered"], true);
    let queued_count = count_path(payload, &["queue_overview", "queued_count"])
        .max(count_path(payload, &["queue", "queued_count"]));
    let running_count = count_path(payload, &["queue_overview", "running_now_count"])
        .max(count_path(payload, &["queue", "running_now_count"]));
    let approval_count = count_path(payload, &["queue_overview", "blocked_on_approval_count"])
        .max(count_path(payload, &["queue", "blocked_on_approval_count"]));
    let pending_wakeup_count = count_path(payload, &["wake_queue", "pending_count"]).max(
        count_path(payload, &["queue_overview", "pending_wakeup_count"]),
    );
    let claimed_wakeup_count = count_path(payload, &["wake_queue", "claimed_count"]);
    let failed_count = count_path(payload, &["queue", "failed_count"])
        .max(count_path(payload, &["retry", "failed_count"]))
        .max(count_path(payload, &["lane_queue", "failed_count"]));
    let retry_pending_count = count_path(payload, &["retry", "pending_count"])
        .max(count_path(payload, &["retry", "retry_pending_count"]))
        .max(count_path(payload, &["queue", "retry_pending_count"]));
    let reminders_count = count_path(payload, &["reminders", "count"]);
    let stale_heartbeat_count = count_path(payload, &["runtime", "stale_heartbeat_count"]).max(
        count_path(payload, &["lane_queue", "stale_heartbeat_count"]),
    );
    let unavailable_worker_count = count_path(payload, &["runtime", "unavailable_worker_count"])
        .max(count_path(
            payload,
            &["lane_queue", "unavailable_worker_count"],
        ));
    let snapshot_age_seconds = number(payload, &["snapshot_age_seconds"], 0);
    let max_snapshot_age_seconds = number(payload, &["max_snapshot_age_seconds"], 120).max(1);
    let max_queue_pressure = number(payload, &["max_queue_pressure"], 50).max(1);

    let mut blocks: Vec<String> = Vec::new();
    let mut warnings: Vec<String> = Vec::new();
    let mut flags: Vec<&str> = Vec::new();

    if operation.is_empty() {
        blocks.push("runtime_health_operation_missing".to_string());
    } else if !OPERATIONS.contains(&operation.as_str()) {
        blocks.push(format!("unknown_runtime_health_operation:{operation}"));
    }
    if workspace_id.is_empty()
        && matches!(
            operation.as_str(),
            "heartbeat_snapshot" | "readiness_gate" | "operator_next_action"
        )
    {
        blocks.push("workspace_id_missing".to_string());
    }
    if snapshot_age_seconds > max_snapshot_age_seconds {
        warnings.push("heartbeat_snapshot_stale".to_string());
        flags.push("stale_snapshot");
    }
    if !rust_kernel_available {
        warnings.push("rust_kernel_unavailable".to_string());
        flags.push("rust_kernel_unavailable");
    }
    if !scheduler_enabled {
        warnings.push("scheduler_disabled".to_string());
        flags.push("scheduler_disabled");
    }
    if !plugin_ok {
        warnings.push("plugin_system_unavailable".to_string());
        flags.push("plugin_unavailable");
    }
    if !profile_complete {
        warnings.push("profile_bootstrap_incomplete".to_string());
        flags.push("bootstrap_incomplete");
    }
    if !ambient_registered
        && matches!(
            operation.as_str(),
            "readiness_gate" | "operator_next_action"
        )
    {
        warnings.push("ambient_monitor_not_registered".to_string());
        flags.push("ambient_monitor_missing");
    }
    if approval_count > 0 {
        flags.push("approval_required");
    }
    if quiet_hours_active {
        flags.push("quiet_hours_active");
    }
    if failed_count > 0 {
        warnings.push("runtime_failures_present".to_string());
        flags.push("runtime_failures_present");
    }
    if stale_heartbeat_count > 0 || unavailable_worker_count > 0 {
        warnings.push("runtime_worker_health_degraded".to_string());
        flags.push("worker_health_degraded");
    }
    if queued_count + pending_wakeup_count > max_queue_pressure {
        warnings.push("runtime_queue_pressure_high".to_string());
        flags.push("queue_pressure_high");
    }

    blocks.sort();
    blocks.dedup();
    warnings.sort();
    warnings.dedup();
    flags.sort();
    flags.dedup();

    let health = health_label(
        !blocks.is_empty(),
        rust_kernel_available,
        scheduler_enabled,
        plugin_ok,
        profile_complete,
        failed_count,
        stale_heartbeat_count,
        unavailable_worker_count,
        approval_count,
        quiet_hours_active,
        queued_count + pending_wakeup_count,
        max_queue_pressure,
    );
    let next_action = next_action(
        &health,
        approval_count,
        running_count,
        retry_pending_count,
        queued_count,
        pending_wakeup_count,
        quiet_hours_active,
        profile_complete,
        rust_kernel_available,
        plugin_ok,
    );
    let readiness_blocked =
        operation == "readiness_gate" && matches!(health.as_str(), "critical" | "degraded");
    let decision = if !blocks.is_empty() || readiness_blocked {
        "block"
    } else {
        "allow"
    };
    let reason = if !blocks.is_empty() {
        blocks.join("; ")
    } else if readiness_blocked {
        "runtime_health_readiness_gate_failed".to_string()
    } else {
        "runtime_health_snapshot_normalized".to_string()
    };

    json!({
        "ok": decision != "block",
        "decision": decision,
        "reason": reason,
        "operation": operation,
        "health": health,
        "next_action": next_action,
        "workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "readiness": {
            "ready": decision != "block" && matches!(health.as_str(), "healthy" | "quiet" | "needs_approval" | "busy"),
            "rust_kernel_available": rust_kernel_available,
            "scheduler_enabled": scheduler_enabled,
            "plugin_ok": plugin_ok,
            "profile_complete": profile_complete,
            "ambient_registered": ambient_registered,
            "snapshot_age_seconds": snapshot_age_seconds,
            "max_snapshot_age_seconds": max_snapshot_age_seconds,
        },
        "queue": {
            "queued_count": queued_count,
            "running_now_count": running_count,
            "blocked_on_approval_count": approval_count,
            "pending_wakeup_count": pending_wakeup_count,
            "claimed_wakeup_count": claimed_wakeup_count,
            "failed_count": failed_count,
            "retry_pending_count": retry_pending_count,
            "reminders_count": reminders_count,
            "pressure": queued_count + pending_wakeup_count,
            "max_pressure": max_queue_pressure,
        },
        "runtime": {
            "stale_heartbeat_count": stale_heartbeat_count,
            "unavailable_worker_count": unavailable_worker_count,
            "quiet_hours_active": quiet_hours_active,
        },
        "normalized_flags": flags,
        "warnings": warnings,
        "block_reasons": blocks,
        "audit_visibility": if matches!(health.as_str(), "healthy" | "quiet") { "standard" } else { "security" },
        "cacheable": false,
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

fn text_path(payload: &Value, path: &[&str]) -> String {
    path_value(payload, path)
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string()
}

fn number(payload: &Value, keys: &[&str], default: u64) -> u64 {
    for key in keys {
        if let Some(value) = payload.get(*key) {
            if let Some(number) = value.as_u64() {
                return number;
            }
            if let Some(text) = value.as_str() {
                if let Ok(parsed) = text.trim().parse::<u64>() {
                    return parsed;
                }
            }
        }
    }
    default
}

fn count_path(payload: &Value, path: &[&str]) -> u64 {
    path_value(payload, path)
        .and_then(Value::as_u64)
        .unwrap_or(0)
}

fn bool_path(payload: &Value, path: &[&str], default: bool) -> bool {
    let Some(value) = path_value(payload, path) else {
        return default;
    };
    if let Some(flag) = value.as_bool() {
        return flag;
    }
    if let Some(text) = value.as_str() {
        return matches!(
            normalize(text).as_str(),
            "1" | "true" | "yes" | "on" | "enabled" | "active" | "ok"
        );
    }
    if let Some(number) = value.as_u64() {
        return number > 0;
    }
    default
}

fn path_value<'a>(payload: &'a Value, path: &[&str]) -> Option<&'a Value> {
    let mut current = payload;
    for key in path {
        current = current.get(*key)?;
    }
    Some(current)
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
        "heartbeat" | "heartbeat_snapshot" => "heartbeat_snapshot".to_string(),
        "readiness" | "readiness_gate" => "readiness_gate".to_string(),
        "queue" | "queue_overview" => "queue_overview".to_string(),
        "next_action" | "operator_next_action" => "operator_next_action".to_string(),
        "plugin" | "plugin_health" => "plugin_health".to_string(),
        "rust_kernel" | "rust_kernel_health" => "rust_kernel_health".to_string(),
        "lane" | "runtime_lane_health" => "runtime_lane_health".to_string(),
        "" => "heartbeat_snapshot".to_string(),
        other => other.to_string(),
    }
}

fn health_label(
    blocked: bool,
    rust_kernel_available: bool,
    scheduler_enabled: bool,
    plugin_ok: bool,
    profile_complete: bool,
    failed_count: u64,
    stale_heartbeat_count: u64,
    unavailable_worker_count: u64,
    approval_count: u64,
    quiet_hours_active: bool,
    queue_pressure: u64,
    max_queue_pressure: u64,
) -> String {
    if blocked || !rust_kernel_available {
        "critical".to_string()
    } else if !scheduler_enabled
        || !plugin_ok
        || failed_count > 0
        || stale_heartbeat_count > 0
        || unavailable_worker_count > 0
    {
        "degraded".to_string()
    } else if !profile_complete {
        "setup_incomplete".to_string()
    } else if approval_count > 0 {
        "needs_approval".to_string()
    } else if quiet_hours_active {
        "quiet".to_string()
    } else if queue_pressure > max_queue_pressure {
        "busy".to_string()
    } else {
        "healthy".to_string()
    }
}

fn next_action(
    health: &str,
    approval_count: u64,
    running_count: u64,
    retry_pending_count: u64,
    queued_count: u64,
    pending_wakeup_count: u64,
    quiet_hours_active: bool,
    profile_complete: bool,
    rust_kernel_available: bool,
    plugin_ok: bool,
) -> &'static str {
    if !rust_kernel_available {
        "restore_rust_kernel"
    } else if !plugin_ok {
        "inspect_plugin_system"
    } else if health == "critical" || health == "degraded" {
        "investigate_runtime_health"
    } else if !profile_complete {
        "complete_workspace_bootstrap"
    } else if approval_count > 0 {
        "request_owner_approval"
    } else if quiet_hours_active {
        "wait_for_quiet_hours"
    } else if running_count > 0 {
        "monitor_running_work"
    } else if retry_pending_count > 0 {
        "wait_for_retry"
    } else if queued_count > 0 || pending_wakeup_count > 0 {
        "claim_next_work"
    } else {
        "idle"
    }
}

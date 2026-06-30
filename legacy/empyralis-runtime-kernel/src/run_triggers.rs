use serde_json::{json, Value};

const WEEKDAYS: &[&str] = &[
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
];

pub fn run_trigger_decision_command(input: &Value) -> Value {
    let operation = normalize_operation(
        string_field(input, "operation")
            .or_else(|| string_field(input, "action"))
            .or_else(|| string_field(input, "intent"))
            .as_deref()
            .unwrap_or(""),
    );
    if operation.is_empty() {
        return block("run_trigger_operation_missing");
    }

    let workspace_id = string_field(input, "workspace_id");
    let schedule_id = string_field(input, "schedule_id").or_else(|| string_field(input, "id"));
    let trigger_id =
        string_field(input, "trigger_id").or_else(|| string_field(input, "webhook_trigger_id"));
    let actor_role = normalize_actor_role(
        string_field(input, "actor_role")
            .or_else(|| string_field(input, "user_role"))
            .or_else(|| string_field(input, "role"))
            .as_deref()
            .unwrap_or("viewer"),
    );
    let schedule_kind = normalize_schedule_kind(
        string_field(input, "schedule_kind")
            .or_else(|| string_field(input, "kind"))
            .as_deref()
            .unwrap_or("cron"),
    );
    let timezone = normalize_timezone(
        string_field(input, "timezone")
            .as_deref()
            .unwrap_or("local"),
    );

    if workspace_id.is_none() && requires_workspace(&operation) {
        return block("workspace_id_missing");
    }

    match operation.as_str() {
        "list_schedules" => read_decision(
            "run_trigger_list_schedules_allowed",
            &operation,
            "list_schedules",
            workspace_id,
            schedule_id,
            trigger_id,
            &actor_role,
            &schedule_kind,
            &timezone,
            true,
        ),
        "read_schedule" => {
            if schedule_id.is_none() {
                return block("schedule_id_missing");
            }
            read_decision(
                "run_trigger_read_schedule_allowed",
                &operation,
                "read_schedule",
                workspace_id,
                schedule_id,
                trigger_id,
                &actor_role,
                &schedule_kind,
                &timezone,
                true,
            )
        }
        "create_schedule" => create_or_update_schedule_decision(
            input,
            &operation,
            "create_schedule",
            workspace_id,
            schedule_id,
            trigger_id,
            actor_role,
            schedule_kind,
            timezone,
        ),
        "update_schedule" => {
            if schedule_id.is_none() {
                return block("schedule_id_missing");
            }
            create_or_update_schedule_decision(
                input,
                &operation,
                "update_schedule",
                workspace_id,
                schedule_id,
                trigger_id,
                actor_role,
                schedule_kind,
                timezone,
            )
        }
        "delete_schedule" => {
            if schedule_id.is_none() {
                return block("schedule_id_missing");
            }
            if !can_write(&actor_role) {
                return block("run_trigger_actor_cannot_write");
            }
            allow(
                "run_trigger_delete_schedule_allowed",
                &operation,
                "delete_schedule",
                workspace_id,
                schedule_id,
                trigger_id,
                &actor_role,
                &schedule_kind,
                &timezone,
                false,
                "security",
            )
        }
        "trigger_schedule_now" => trigger_schedule_now_decision(
            input,
            workspace_id,
            schedule_id,
            trigger_id,
            actor_role,
            schedule_kind,
            timezone,
        ),
        "trigger_pending_heartbeat" => pending_heartbeat_decision(
            input,
            workspace_id,
            schedule_id,
            trigger_id,
            actor_role,
            schedule_kind,
            timezone,
        ),
        "register_webhook" => register_webhook_decision(
            input,
            workspace_id,
            schedule_id,
            trigger_id,
            actor_role,
            schedule_kind,
            timezone,
        ),
        "ingest_webhook" => ingest_webhook_decision(
            input,
            workspace_id,
            schedule_id,
            trigger_id,
            actor_role,
            schedule_kind,
            timezone,
        ),
        _ => block("run_trigger_operation_unknown"),
    }
}

fn read_decision(
    reason: &str,
    operation: &str,
    next_action: &str,
    workspace_id: Option<String>,
    schedule_id: Option<String>,
    trigger_id: Option<String>,
    actor_role: &str,
    schedule_kind: &str,
    timezone: &str,
    cacheable: bool,
) -> Value {
    if !can_read(actor_role) {
        return block("run_trigger_actor_cannot_read");
    }
    allow(
        reason,
        operation,
        next_action,
        workspace_id,
        schedule_id,
        trigger_id,
        actor_role,
        schedule_kind,
        timezone,
        cacheable,
        "standard",
    )
}

fn create_or_update_schedule_decision(
    input: &Value,
    operation: &str,
    next_action: &str,
    workspace_id: Option<String>,
    schedule_id: Option<String>,
    trigger_id: Option<String>,
    actor_role: String,
    schedule_kind: String,
    timezone: String,
) -> Value {
    if !can_write(&actor_role) {
        return block("run_trigger_actor_cannot_write");
    }
    if !matches!(schedule_kind.as_str(), "cron" | "weekly") {
        return block("schedule_kind_unknown");
    }
    if !matches!(timezone.as_str(), "local" | "utc") {
        return block("schedule_timezone_unknown");
    }
    if schedule_kind == "cron" && string_field(input, "cron").is_none() {
        return block("schedule_cron_missing");
    }
    if schedule_kind == "weekly" {
        let weekday = normalize_token(string_field(input, "day_of_week").as_deref().unwrap_or(""));
        if !WEEKDAYS.contains(&weekday.as_str()) {
            return block("weekly_schedule_day_invalid");
        }
        if !valid_time_hhmm(string_field(input, "time_hhmm").as_deref().unwrap_or("")) {
            return block("weekly_schedule_time_invalid");
        }
    }
    if !boolish(input, "run_request_present", false) && operation == "create_schedule" {
        return block("schedule_run_request_missing");
    }
    if boolish(input, "run_request_present", false) && boolish(input, "run_request_invalid", false)
    {
        return block("schedule_run_request_invalid");
    }
    allow(
        "run_trigger_schedule_write_allowed",
        operation,
        next_action,
        workspace_id,
        schedule_id,
        trigger_id,
        &actor_role,
        &schedule_kind,
        &timezone,
        false,
        "security",
    )
}

fn trigger_schedule_now_decision(
    input: &Value,
    workspace_id: Option<String>,
    schedule_id: Option<String>,
    trigger_id: Option<String>,
    actor_role: String,
    schedule_kind: String,
    timezone: String,
) -> Value {
    if schedule_id.is_none() {
        return block("schedule_id_missing");
    }
    if !can_write(&actor_role) {
        return block("run_trigger_actor_cannot_write");
    }
    if !boolish(input, "schedule_exists", true) {
        return block("schedule_not_found");
    }
    if !boolish(input, "run_request_present", true) {
        return block("schedule_run_request_missing");
    }
    allow(
        "run_trigger_schedule_now_allowed",
        "trigger_schedule_now",
        "execute_scheduled_run",
        workspace_id,
        schedule_id,
        trigger_id,
        &actor_role,
        &schedule_kind,
        &timezone,
        false,
        "security",
    )
}

fn pending_heartbeat_decision(
    input: &Value,
    workspace_id: Option<String>,
    schedule_id: Option<String>,
    trigger_id: Option<String>,
    actor_role: String,
    schedule_kind: String,
    timezone: String,
) -> Value {
    if !boolish(input, "scheduler_enabled", true) {
        return block("scheduler_disabled");
    }
    if positive_i64(input, "pending_count") == 0 {
        return allow(
            "run_trigger_no_pending_heartbeats",
            "trigger_pending_heartbeat",
            "noop",
            workspace_id,
            schedule_id,
            trigger_id,
            &actor_role,
            &schedule_kind,
            &timezone,
            true,
            "standard",
        );
    }
    allow(
        "run_trigger_pending_heartbeats_allowed",
        "trigger_pending_heartbeat",
        "trigger_pending_schedules",
        workspace_id,
        schedule_id,
        trigger_id,
        &actor_role,
        &schedule_kind,
        &timezone,
        false,
        "security",
    )
}

fn register_webhook_decision(
    input: &Value,
    workspace_id: Option<String>,
    schedule_id: Option<String>,
    trigger_id: Option<String>,
    actor_role: String,
    schedule_kind: String,
    timezone: String,
) -> Value {
    if !can_write(&actor_role) {
        return block("run_trigger_actor_cannot_write");
    }
    if string_field(input, "url_pattern").is_none() {
        return block("webhook_url_pattern_missing");
    }
    if string_field(input, "workflow_id").is_none() {
        return block("webhook_workflow_id_missing");
    }
    allow(
        "run_trigger_register_webhook_allowed",
        "register_webhook",
        "register_webhook_trigger",
        workspace_id,
        schedule_id,
        trigger_id,
        &actor_role,
        &schedule_kind,
        &timezone,
        false,
        "security",
    )
}

fn ingest_webhook_decision(
    input: &Value,
    workspace_id: Option<String>,
    schedule_id: Option<String>,
    trigger_id: Option<String>,
    actor_role: String,
    schedule_kind: String,
    timezone: String,
) -> Value {
    if !boolish(input, "trigger_matched", false) {
        return block("webhook_trigger_not_matched");
    }
    if !boolish(input, "trigger_enabled", true) {
        return block("webhook_trigger_disabled");
    }
    if string_field(input, "workflow_id").is_none() {
        return block("webhook_workflow_id_missing");
    }
    allow(
        "run_trigger_ingest_webhook_allowed",
        "ingest_webhook",
        "execute_webhook_run",
        workspace_id,
        schedule_id,
        trigger_id,
        &actor_role,
        &schedule_kind,
        &timezone,
        false,
        "security",
    )
}

fn allow(
    reason: &str,
    operation: &str,
    next_action: &str,
    workspace_id: Option<String>,
    schedule_id: Option<String>,
    trigger_id: Option<String>,
    actor_role: &str,
    schedule_kind: &str,
    timezone: &str,
    cacheable: bool,
    audit_visibility: &str,
) -> Value {
    json!({
        "ok": true,
        "decision": "allow",
        "reason": reason,
        "operation": operation,
        "next_action": next_action,
        "workspace_id": workspace_id,
        "schedule_id": schedule_id,
        "trigger_id": trigger_id,
        "actor_role": actor_role,
        "schedule_kind": schedule_kind,
        "timezone": timezone,
        "approval_required": false,
        "cacheable": cacheable,
        "audit_visibility": audit_visibility,
    })
}

fn block(reason: &str) -> Value {
    json!({
        "ok": false,
        "decision": "block",
        "reason": reason,
        "approval_required": false,
        "cacheable": false,
        "audit_visibility": "security",
    })
}

fn normalize_operation(value: &str) -> String {
    match normalize_token(value).as_str() {
        "list_schedules" | "schedules_list" => "list_schedules".to_string(),
        "read_schedule" | "get_schedule" | "schedule_logs" => "read_schedule".to_string(),
        "create_schedule" | "create_weekly_schedule" => "create_schedule".to_string(),
        "update_schedule" | "update_weekly_schedule" => "update_schedule".to_string(),
        "delete_schedule" | "delete_weekly_schedule" => "delete_schedule".to_string(),
        "trigger_schedule_now" | "trigger_weekly_schedule_now" => {
            "trigger_schedule_now".to_string()
        }
        "trigger_pending_heartbeat" | "pending_heartbeat_schedules" => {
            "trigger_pending_heartbeat".to_string()
        }
        "register_webhook" | "register_webhook_trigger" => "register_webhook".to_string(),
        "ingest_webhook" | "webhook_ingest" | "webhook_trigger" => "ingest_webhook".to_string(),
        _ => String::new(),
    }
}

fn normalize_schedule_kind(value: &str) -> String {
    match normalize_token(value).as_str() {
        "" | "cron" | "schedule" => "cron".to_string(),
        "weekly" | "week" => "weekly".to_string(),
        other => other.to_string(),
    }
}

fn normalize_timezone(value: &str) -> String {
    match normalize_token(value).as_str() {
        "" | "local" => "local".to_string(),
        "utc" => "utc".to_string(),
        other => other.to_string(),
    }
}

fn normalize_actor_role(value: &str) -> String {
    match normalize_token(value).as_str() {
        "system" | "service" => "system".to_string(),
        "owner" | "admin" => "owner".to_string(),
        "editor" | "maintainer" | "member" => "member".to_string(),
        "viewer" | "readonly" | "read_only" => "viewer".to_string(),
        _ => "viewer".to_string(),
    }
}

fn normalize_token(value: &str) -> String {
    value
        .trim()
        .to_ascii_lowercase()
        .replace('-', "_")
        .replace('.', "_")
        .replace(' ', "_")
}

fn string_field(input: &Value, key: &str) -> Option<String> {
    input.get(key).and_then(Value::as_str).and_then(|value| {
        let trimmed = value.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_string())
        }
    })
}

fn boolish(input: &Value, key: &str, default_value: bool) -> bool {
    if input.get(key).is_none() {
        return default_value;
    }
    match input.get(key) {
        Some(Value::Bool(value)) => *value,
        Some(Value::String(value)) => matches!(
            normalize_token(value).as_str(),
            "1" | "true" | "yes" | "on" | "enabled" | "active" | "present" | "valid"
        ),
        Some(Value::Number(value)) => value.as_i64().unwrap_or(0) > 0,
        _ => false,
    }
}

fn positive_i64(input: &Value, key: &str) -> i64 {
    match input.get(key) {
        Some(Value::Number(value)) => value.as_i64().unwrap_or(0).max(0),
        Some(Value::String(value)) => value.trim().parse::<i64>().unwrap_or(0).max(0),
        _ => 0,
    }
}

fn valid_time_hhmm(value: &str) -> bool {
    let parts: Vec<&str> = value.trim().split(':').collect();
    if parts.len() != 2 {
        return false;
    }
    let hour = parts[0].parse::<u8>();
    let minute = parts[1].parse::<u8>();
    match (hour, minute) {
        (Ok(hour), Ok(minute)) => hour < 24 && minute < 60,
        _ => false,
    }
}

fn requires_workspace(operation: &str) -> bool {
    !matches!(operation, "trigger_pending_heartbeat")
}

fn can_read(actor_role: &str) -> bool {
    matches!(actor_role, "system" | "owner" | "member" | "viewer")
}

fn can_write(actor_role: &str) -> bool {
    matches!(actor_role, "system" | "owner" | "member")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blocks_missing_operation() {
        let response = run_trigger_decision_command(&json!({"workspace_id": "w1"}));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "run_trigger_operation_missing");
    }

    #[test]
    fn create_weekly_schedule_requires_valid_time() {
        let response = run_trigger_decision_command(&json!({
            "operation": "create_schedule",
            "workspace_id": "w1",
            "schedule_kind": "weekly",
            "day_of_week": "Monday",
            "time_hhmm": "25:00",
            "run_request_present": true,
            "actor_role": "owner"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "weekly_schedule_time_invalid");
    }

    #[test]
    fn create_cron_schedule_requires_run_request() {
        let response = run_trigger_decision_command(&json!({
            "operation": "create_schedule",
            "workspace_id": "w1",
            "schedule_kind": "cron",
            "cron": "0 9 * * 1",
            "actor_role": "owner"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "schedule_run_request_missing");
    }

    #[test]
    fn trigger_schedule_now_allows_existing_schedule() {
        let response = run_trigger_decision_command(&json!({
            "operation": "trigger_schedule_now",
            "workspace_id": "w1",
            "schedule_id": "s1",
            "actor_role": "owner"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_action"], "execute_scheduled_run");
    }

    #[test]
    fn register_webhook_requires_workflow() {
        let response = run_trigger_decision_command(&json!({
            "operation": "register_webhook",
            "workspace_id": "w1",
            "url_pattern": "/webhooks/*",
            "actor_role": "owner"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "webhook_workflow_id_missing");
    }

    #[test]
    fn ingest_webhook_requires_match() {
        let response = run_trigger_decision_command(&json!({
            "operation": "ingest_webhook",
            "workspace_id": "w1",
            "workflow_id": "wf1"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "webhook_trigger_not_matched");
    }
}

#[cfg(test)]
mod run_trigger_kernel_boundary_tests {
    use super::run_trigger_decision_command;

    #[test]
    fn register_webhook_requires_workflow_id() {
        let decision = run_trigger_decision_command(&serde_json::json!({
            "operation": "register_webhook",
            "workspace_id": "default",
            "actor_role": "system",
            "url_pattern": "/hook"
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("block")
        );
        assert_eq!(
            decision.get("reason").and_then(|value| value.as_str()),
            Some("webhook_workflow_id_missing")
        );
    }

    #[test]
    fn ingest_webhook_blocks_unmatched_trigger() {
        let decision = run_trigger_decision_command(&serde_json::json!({
            "operation": "ingest_webhook",
            "workspace_id": "default",
            "actor_role": "system",
            "trigger_matched": false,
            "workflow_id": "wf-1"
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("block")
        );
        assert_eq!(
            decision.get("reason").and_then(|value| value.as_str()),
            Some("webhook_trigger_not_matched")
        );
    }
}

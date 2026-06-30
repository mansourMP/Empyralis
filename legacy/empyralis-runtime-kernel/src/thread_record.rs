use serde_json::{json, Value};

pub fn thread_record_decision_command(payload: &Value) -> Value {
    let operation = normalize_operation(&text_any(payload, &["operation", "action"]));
    if operation.is_empty() {
        return block("thread_record_operation_missing", "unknown");
    }
    if !known_operation(&operation) {
        return block("thread_record_operation_unknown", &operation);
    }

    let workspace_id = text_any(payload, &["workspace_id", "owner_workspace_id"]);
    if workspace_id.is_empty() {
        return block("workspace_id_missing", &operation);
    }
    let tenant_id = text_any(payload, &["tenant_id", "owner_tenant_id"]);
    if tenant_id.is_empty() {
        return block("tenant_id_missing", &operation);
    }
    if bool_field(payload, "workspace_access_denied") {
        return block("thread_workspace_access_denied", &operation);
    }

    let role = actor_role(payload);
    match operation.as_str() {
        "list_threads" => list_threads_decision(payload, &tenant_id, &workspace_id, &role),
        "get_thread" => get_thread_decision(payload, &tenant_id, &workspace_id, &role),
        "create_turn" => create_turn_decision(payload, &tenant_id, &workspace_id, &role),
        "normalize_thread" => normalize_thread_decision(payload, &tenant_id, &workspace_id, &role),
        "normalize_turn" => normalize_turn_decision(payload, &tenant_id, &workspace_id, &role),
        "history_filter" => history_filter_decision(payload, &tenant_id, &workspace_id, &role),
        _ => block("thread_record_operation_unknown", &operation),
    }
}

fn list_threads_decision(
    payload: &Value,
    tenant_id: &str,
    workspace_id: &str,
    role: &str,
) -> Value {
    if !can_view(role) {
        return block("thread_viewer_required", "list_threads");
    }
    if !history_window_allowed(payload) {
        return block("thread_history_window_exceeded", "list_threads");
    }
    let limit = page_limit(payload, 50, 200);
    allow(
        "thread_list_allowed",
        "list_threads",
        "list_workspace_threads",
        tenant_id,
        workspace_id,
        "",
        role,
        true,
        "standard",
    )
    .with_extra("limit", json!(limit))
    .with_extra("include_turns", json!(bool_field(payload, "include_turns")))
    .with_extra("owner_scope_required", json!(!is_privileged(role)))
}

fn get_thread_decision(payload: &Value, tenant_id: &str, workspace_id: &str, role: &str) -> Value {
    if !can_view(role) {
        return block("thread_viewer_required", "get_thread");
    }
    let thread_id = text_any(payload, &["thread_id", "id"]);
    if thread_id.is_empty() {
        return block("thread_id_missing", "get_thread");
    }
    if bool_field(payload, "owner_mismatch") && !is_privileged(role) {
        return block("thread_not_found", "get_thread");
    }
    if bool_field(payload, "record_missing") {
        if thread_id == "primary" {
            return allow(
                "thread_primary_fallback_allowed",
                "get_thread",
                "return_empty_primary_thread",
                tenant_id,
                workspace_id,
                &thread_id,
                role,
                false,
                "standard",
            );
        }
        return block("thread_not_found", "get_thread");
    }
    if !history_window_allowed(payload) {
        return block("thread_history_window_exceeded", "get_thread");
    }
    allow(
        "thread_get_allowed",
        "get_thread",
        "get_thread_with_turns",
        tenant_id,
        workspace_id,
        &thread_id,
        role,
        true,
        "standard",
    )
}

fn create_turn_decision(payload: &Value, tenant_id: &str, workspace_id: &str, role: &str) -> Value {
    if !can_write(role) {
        return block("thread_member_required", "create_turn");
    }
    let thread_id = text_any(payload, &["thread_id", "id"]);
    if thread_id.is_empty() {
        return block("thread_id_missing", "create_turn");
    }
    if bool_field(payload, "owner_mismatch") && !is_privileged(role) {
        return block("thread_not_found", "create_turn");
    }
    let content = text_any(payload, &["content", "message", "text"]);
    let has_metadata_only_turn = bool_field(payload, "metadata_only_turn");
    if content.is_empty() && !has_metadata_only_turn {
        return block("thread_turn_content_missing", "create_turn");
    }
    allow(
        "thread_turn_create_allowed",
        "create_turn",
        "create_thread_turn",
        tenant_id,
        workspace_id,
        &thread_id,
        role,
        false,
        "security",
    )
    .with_extra(
        "request_id",
        json!(text_any(payload, &["client_request_id", "request_id"])),
    )
}

fn normalize_thread_decision(
    payload: &Value,
    tenant_id: &str,
    workspace_id: &str,
    role: &str,
) -> Value {
    if !can_view(role) {
        return block("thread_viewer_required", "normalize_thread");
    }
    let thread_id = text_any(payload, &["thread_id", "id"]);
    allow(
        "thread_normalize_allowed",
        "normalize_thread",
        "normalize_thread_record",
        tenant_id,
        workspace_id,
        &thread_id,
        role,
        true,
        "standard",
    )
    .with_extra(
        "normalized_title",
        json!(defaulted_text(payload, &["title"], "New chat")),
    )
    .with_extra(
        "normalized_status",
        json!(defaulted_text(payload, &["status"], "active")),
    )
}

fn normalize_turn_decision(
    payload: &Value,
    tenant_id: &str,
    workspace_id: &str,
    role: &str,
) -> Value {
    if !can_view(role) {
        return block("thread_viewer_required", "normalize_turn");
    }
    let thread_id = text_any(payload, &["thread_id"]);
    if thread_id.is_empty() {
        return block("thread_id_missing", "normalize_turn");
    }
    allow(
        "thread_turn_normalize_allowed",
        "normalize_turn",
        "normalize_thread_turn_record",
        tenant_id,
        workspace_id,
        &thread_id,
        role,
        true,
        "standard",
    )
    .with_extra(
        "normalized_role",
        json!(defaulted_text(payload, &["turn_role", "role"], "assistant")),
    )
    .with_extra("normalized_status", json!(text_any(payload, &["status"])))
}

fn history_filter_decision(
    payload: &Value,
    tenant_id: &str,
    workspace_id: &str,
    role: &str,
) -> Value {
    if !can_view(role) {
        return block("thread_viewer_required", "history_filter");
    }
    if !history_window_allowed(payload) {
        return block("thread_history_window_exceeded", "history_filter");
    }
    allow(
        "thread_history_filter_allowed",
        "history_filter",
        "include_history_record",
        tenant_id,
        workspace_id,
        &text_any(payload, &["thread_id", "id"]),
        role,
        true,
        "standard",
    )
    .with_extra("history_window_days", json!(history_window_days(payload)))
}

fn allow(
    reason: &str,
    operation: &str,
    next_action: &str,
    tenant_id: &str,
    workspace_id: &str,
    thread_id: &str,
    role: &str,
    cacheable: bool,
    audit_visibility: &str,
) -> Value {
    json!({
        "ok": true,
        "decision": "allow",
        "reason": reason,
        "operation": operation,
        "next_action": next_action,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "thread_id": thread_id,
        "actor_role": role,
        "approval_required": false,
        "cacheable": cacheable,
        "audit_visibility": audit_visibility,
    })
}

fn block(reason: &str, operation: &str) -> Value {
    json!({
        "ok": false,
        "decision": "block",
        "reason": reason,
        "operation": operation,
        "approval_required": false,
        "cacheable": false,
        "audit_visibility": "security",
    })
}

trait JsonExtra {
    fn with_extra(self, key: &str, value: Value) -> Value;
}

impl JsonExtra for Value {
    fn with_extra(self, key: &str, value: Value) -> Value {
        match self {
            Value::Object(mut map) => {
                map.insert(key.to_string(), value);
                Value::Object(map)
            }
            other => other,
        }
    }
}

fn normalize_operation(raw: &str) -> String {
    match normalize_token(raw).as_str() {
        "list" | "threads" => "list_threads".to_string(),
        "get" | "detail" | "thread_detail" => "get_thread".to_string(),
        "turn_create" | "create_thread_turn" | "append_turn" => "create_turn".to_string(),
        "thread_normalize" => "normalize_thread".to_string(),
        "turn_normalize" => "normalize_turn".to_string(),
        "history" | "history_window" => "history_filter".to_string(),
        other => other.to_string(),
    }
}

fn known_operation(operation: &str) -> bool {
    matches!(
        operation,
        "list_threads"
            | "get_thread"
            | "create_turn"
            | "normalize_thread"
            | "normalize_turn"
            | "history_filter"
    )
}

fn actor_role(payload: &Value) -> String {
    let role = text_any(
        payload,
        &["actor_role", "user_role", "role", "minimum_role"],
    );
    if !role.is_empty() {
        return normalize_token(&role);
    }
    if bool_field(payload, "current_user_is_privileged") || bool_field(payload, "is_admin") {
        return "admin".to_string();
    }
    "viewer".to_string()
}

fn can_view(role: &str) -> bool {
    matches!(
        role,
        "viewer"
            | "member"
            | "admin"
            | "owner"
            | "workspace_admin"
            | "tenant_admin"
            | "system"
            | "service"
    )
}

fn can_write(role: &str) -> bool {
    matches!(
        role,
        "member" | "admin" | "owner" | "workspace_admin" | "tenant_admin" | "system" | "service"
    )
}

fn is_privileged(role: &str) -> bool {
    matches!(
        role,
        "admin" | "owner" | "workspace_admin" | "tenant_admin" | "system" | "service"
    )
}

fn history_window_allowed(payload: &Value) -> bool {
    if bool_field(payload, "history_window_allowed") {
        return true;
    }
    if explicit_false(payload, "history_window_allowed") {
        return false;
    }
    if history_window_days(payload) > 365 {
        return false;
    }
    let Some(age_seconds) = numeric_any(
        payload,
        &["age_seconds", "record_age_seconds", "turn_age_seconds"],
    ) else {
        return true;
    };
    let cutoff_seconds = history_window_days(payload).saturating_mul(86_400);
    age_seconds <= cutoff_seconds
}

fn history_window_days(payload: &Value) -> u64 {
    numeric_any(payload, &["history_window_days"])
        .unwrap_or(30)
        .max(1)
}

fn page_limit(payload: &Value, default_value: u64, max_value: u64) -> u64 {
    numeric_any(payload, &["limit"])
        .unwrap_or(default_value)
        .clamp(1, max_value)
}

fn defaulted_text(payload: &Value, keys: &[&str], default_value: &str) -> String {
    let value = text_any(payload, keys);
    if value.is_empty() {
        default_value.to_string()
    } else {
        value
    }
}

fn text_any(payload: &Value, keys: &[&str]) -> String {
    for key in keys {
        if let Some(value) = payload.get(*key).and_then(Value::as_str) {
            let trimmed = value.trim();
            if !trimmed.is_empty() {
                return trimmed.to_string();
            }
        }
    }
    String::new()
}

fn numeric_any(payload: &Value, keys: &[&str]) -> Option<u64> {
    for key in keys {
        match payload.get(*key) {
            Some(Value::Number(value)) => {
                if let Some(parsed) = value.as_u64() {
                    return Some(parsed);
                }
            }
            Some(Value::String(value)) => {
                if let Ok(parsed) = value.trim().parse::<u64>() {
                    return Some(parsed);
                }
            }
            _ => {}
        }
    }
    None
}

fn bool_field(payload: &Value, key: &str) -> bool {
    match payload.get(key) {
        Some(Value::Bool(value)) => *value,
        Some(Value::String(value)) => matches!(
            value.trim().to_ascii_lowercase().as_str(),
            "1" | "true" | "yes" | "y" | "enabled"
        ),
        _ => false,
    }
}

fn explicit_false(payload: &Value, key: &str) -> bool {
    match payload.get(key) {
        Some(Value::Bool(value)) => !*value,
        Some(Value::String(value)) => matches!(
            value.trim().to_ascii_lowercase().as_str(),
            "0" | "false" | "no" | "n" | "disabled"
        ),
        _ => false,
    }
}

fn normalize_token(raw: &str) -> String {
    raw.trim().to_ascii_lowercase().replace('-', "_")
}

#[cfg(test)]
mod thread_record_kernel_boundary_tests {
    use super::thread_record_decision_command;

    #[test]
    fn create_turn_requires_thread_identity() {
        let decision = thread_record_decision_command(&serde_json::json!({
            "operation": "create_turn",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_role": "member",
            "content": "hello"
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("block")
        );
        assert_eq!(
            decision.get("reason").and_then(|value| value.as_str()),
            Some("thread_id_missing")
        );
    }

    #[test]
    fn list_threads_enforces_history_window() {
        let decision = thread_record_decision_command(&serde_json::json!({
            "operation": "list_threads",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_role": "member",
            "history_window_days": 99999
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("block")
        );
        assert_eq!(
            decision.get("reason").and_then(|value| value.as_str()),
            Some("thread_history_window_exceeded")
        );
    }
}

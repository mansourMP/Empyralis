use serde_json::{json, Value};

pub fn deployed_data_decision_command(payload: &Value) -> Value {
    let operation = normalize_operation(&text(payload, "operation"));
    if operation.is_empty() {
        return block("missing_operation", "deployed_data_operation");
    }
    if !known_operation(&operation) {
        return block("unknown_operation", "deployed_data_operation");
    }

    let workspace_id = text(payload, "workspace_id");
    if workspace_id.is_empty() {
        return block("missing_workspace_id", &operation);
    }
    let tenant_id = text(payload, "tenant_id");
    if tenant_id.is_empty() {
        return block("missing_tenant_id", &operation);
    }
    let deployed_agent_id = text(payload, "deployed_agent_id");
    if requires_deployed_agent(&operation) && deployed_agent_id.is_empty() {
        return block("missing_deployed_agent_id", &operation);
    }

    let role = actor_role(payload);
    if role.is_empty() {
        return block("actor_role_required", &operation);
    }
    if !is_admin_role(&role) {
        return block("deployed_agent_admin_required", &operation);
    }

    if bool_field(payload, "kill_switch_enabled") {
        return block("kill_switch_enabled", &operation);
    }
    if bool_field(payload, "workspace_disabled") {
        return block("workspace_disabled", &operation);
    }
    if bool_field(payload, "tenant_disabled") {
        return block("tenant_disabled", &operation);
    }
    if bool_field(payload, "data_retention_lock") && operation == "delete_external_user_data" {
        return block("retention_lock_active", &operation);
    }

    if bool_field(payload, "include_raw_payload")
        || bool_field(payload, "include_internal")
        || bool_field(payload, "include_secret_like_values")
    {
        if !is_owner_role(&role) && !bool_field(payload, "sensitive_access_approved") {
            return require_approval(
                "sensitive_deployed_agent_data_requires_approval",
                &operation,
                "request_sensitive_data_approval",
                &tenant_id,
                &workspace_id,
                &deployed_agent_id,
                false,
                "restricted",
            );
        }
    }

    match operation.as_str() {
        "analytics_list" => analytics_decision(payload, &tenant_id, &workspace_id, ""),
        "analytics_detail" => {
            analytics_decision(payload, &tenant_id, &workspace_id, &deployed_agent_id)
        }
        "audit_export" => audit_export_decision(
            payload,
            &role,
            &tenant_id,
            &workspace_id,
            &deployed_agent_id,
        ),
        "list_conversations" => read_decision(
            payload,
            &operation,
            "list_deployed_agent_conversations",
            "customer_transcript_index",
            &tenant_id,
            &workspace_id,
            &deployed_agent_id,
            true,
            "security",
        ),
        "conversation_detail" => {
            conversation_detail_decision(payload, &tenant_id, &workspace_id, &deployed_agent_id)
        }
        "list_memory" => memory_decision(payload, &tenant_id, &workspace_id, &deployed_agent_id),
        "list_activity" => {
            activity_decision(payload, &tenant_id, &workspace_id, &deployed_agent_id)
        }
        "delete_external_user_data" => delete_external_user_data_decision(
            payload,
            &tenant_id,
            &workspace_id,
            &deployed_agent_id,
        ),
        _ => block("unknown_operation", &operation),
    }
}

fn analytics_decision(
    payload: &Value,
    tenant_id: &str,
    workspace_id: &str,
    deployed_agent_id: &str,
) -> Value {
    if explicit_false(payload, "analytics_enabled") {
        return allow(
            "analytics_disabled",
            "return_empty_analytics",
            "analytics_summary",
            tenant_id,
            workspace_id,
            deployed_agent_id,
            true,
            "admin",
            payload,
        );
    }
    allow(
        "analytics_access_allowed",
        "read_deployed_agent_analytics",
        "analytics_summary",
        tenant_id,
        workspace_id,
        deployed_agent_id,
        true,
        "admin",
        payload,
    )
}

fn audit_export_decision(
    payload: &Value,
    role: &str,
    tenant_id: &str,
    workspace_id: &str,
    deployed_agent_id: &str,
) -> Value {
    if explicit_false(payload, "audit_log_enabled") {
        return block("audit_log_disabled", "audit_export");
    }
    if !is_owner_role(role) && !bool_field(payload, "audit_export_approved") {
        return require_approval(
            "audit_export_requires_owner_or_approval",
            "audit_export",
            "request_audit_export_approval",
            tenant_id,
            workspace_id,
            deployed_agent_id,
            false,
            "security_audit",
        );
    }
    allow(
        "audit_export_allowed",
        "export_deployed_agent_audit_log",
        "audit_log",
        tenant_id,
        workspace_id,
        deployed_agent_id,
        false,
        "security_audit",
        payload,
    )
}

fn conversation_detail_decision(
    payload: &Value,
    tenant_id: &str,
    workspace_id: &str,
    deployed_agent_id: &str,
) -> Value {
    let session_id = text(payload, "session_id");
    if session_id.is_empty() {
        return block("missing_session_id", "conversation_detail");
    }
    if explicit_false(payload, "conversation_events_available") {
        return block("conversation_not_found", "conversation_detail");
    }
    allow(
        "conversation_detail_allowed",
        "read_deployed_agent_conversation_detail",
        "customer_transcript_detail",
        tenant_id,
        workspace_id,
        deployed_agent_id,
        false,
        "restricted",
        payload,
    )
}

fn memory_decision(
    payload: &Value,
    tenant_id: &str,
    workspace_id: &str,
    deployed_agent_id: &str,
) -> Value {
    if explicit_false(payload, "memory_enabled") {
        return allow(
            "memory_disabled",
            "return_empty_memory",
            "customer_memory",
            tenant_id,
            workspace_id,
            deployed_agent_id,
            true,
            "restricted",
            payload,
        );
    }
    allow(
        "memory_access_allowed",
        "list_deployed_agent_memory",
        "customer_memory",
        tenant_id,
        workspace_id,
        deployed_agent_id,
        true,
        "restricted",
        payload,
    )
}

fn activity_decision(
    payload: &Value,
    tenant_id: &str,
    workspace_id: &str,
    deployed_agent_id: &str,
) -> Value {
    if explicit_false(payload, "activity_available") {
        return allow(
            "activity_unavailable",
            "return_empty_activity",
            "activity_ledger",
            tenant_id,
            workspace_id,
            deployed_agent_id,
            true,
            "admin",
            payload,
        );
    }
    allow(
        "activity_access_allowed",
        "list_deployed_agent_activity",
        "activity_ledger",
        tenant_id,
        workspace_id,
        deployed_agent_id,
        true,
        "admin",
        payload,
    )
}

fn delete_external_user_data_decision(
    payload: &Value,
    tenant_id: &str,
    workspace_id: &str,
    deployed_agent_id: &str,
) -> Value {
    let external_user_id = text(payload, "external_user_id");
    if external_user_id.is_empty() {
        return block("missing_external_user_id", "delete_external_user_data");
    }
    let channel_key = text(payload, "channel_key");
    if channel_key.is_empty() {
        return block("missing_channel_key", "delete_external_user_data");
    }
    if explicit_false(payload, "privacy_delete_enabled") {
        return block("privacy_delete_disabled", "delete_external_user_data");
    }
    if !bool_field(payload, "privacy_request_verified")
        || !bool_field(payload, "operator_confirmed")
    {
        return require_approval(
            "external_user_delete_requires_verified_request",
            "delete_external_user_data",
            "verify_privacy_request",
            tenant_id,
            workspace_id,
            deployed_agent_id,
            false,
            "security_audit",
        );
    }
    allow(
        "external_user_delete_allowed",
        "purge_deployed_agent_external_user_data",
        "privacy_delete",
        tenant_id,
        workspace_id,
        deployed_agent_id,
        false,
        "security_audit",
        payload,
    )
}

fn read_decision(
    payload: &Value,
    operation: &str,
    next_action: &str,
    data_class: &str,
    tenant_id: &str,
    workspace_id: &str,
    deployed_agent_id: &str,
    cacheable: bool,
    audit_visibility: &str,
) -> Value {
    allow(
        "data_access_allowed",
        next_action,
        data_class,
        tenant_id,
        workspace_id,
        deployed_agent_id,
        cacheable,
        audit_visibility,
        payload,
    )
    .as_object()
    .map(|object| {
        let mut mapped = object.clone();
        mapped.insert("operation".to_string(), json!(operation));
        Value::Object(mapped)
    })
    .unwrap_or_else(|| block("invalid_decision", operation))
}

fn allow(
    reason: &str,
    next_action: &str,
    data_class: &str,
    tenant_id: &str,
    workspace_id: &str,
    deployed_agent_id: &str,
    cacheable: bool,
    audit_visibility: &str,
    payload: &Value,
) -> Value {
    let operation = normalize_operation(&text(payload, "operation"));
    json!({
        "ok": true,
        "decision": "allow",
        "reason": reason,
        "operation": operation,
        "next_action": next_action,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "deployed_agent_id": deployed_agent_id,
        "data_class": data_class,
        "approval_required": false,
        "cacheable": cacheable,
        "audit_visibility": audit_visibility,
        "page": pagination(payload),
    })
}

fn require_approval(
    reason: &str,
    operation: &str,
    next_action: &str,
    tenant_id: &str,
    workspace_id: &str,
    deployed_agent_id: &str,
    cacheable: bool,
    audit_visibility: &str,
) -> Value {
    json!({
        "ok": true,
        "decision": "require_approval",
        "reason": reason,
        "operation": operation,
        "next_action": next_action,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "deployed_agent_id": deployed_agent_id,
        "approval_required": true,
        "approval_scope": format!("deployed-agent-data:{operation}:{workspace_id}:{deployed_agent_id}"),
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
        "audit_visibility": "security_audit",
    })
}

fn normalize_operation(raw: &str) -> String {
    match raw.trim().to_ascii_lowercase().replace('-', "_").as_str() {
        "list_analytics" | "workspace_analytics" => "analytics_list".to_string(),
        "get_analytics" | "agent_analytics" => "analytics_detail".to_string(),
        "export_audit_logs" | "export_audit_log" => "audit_export".to_string(),
        "conversation_list" => "list_conversations".to_string(),
        "get_conversation_detail" => "conversation_detail".to_string(),
        "memory_list" | "list_memory_entries" => "list_memory".to_string(),
        "activity_list" => "list_activity".to_string(),
        "privacy_delete" | "delete_user_data" | "delete_external_user" => {
            "delete_external_user_data".to_string()
        }
        other => other.to_string(),
    }
}

fn known_operation(operation: &str) -> bool {
    matches!(
        operation,
        "analytics_list"
            | "analytics_detail"
            | "audit_export"
            | "list_conversations"
            | "conversation_detail"
            | "list_memory"
            | "list_activity"
            | "delete_external_user_data"
    )
}

fn requires_deployed_agent(operation: &str) -> bool {
    !matches!(operation, "analytics_list")
}

fn actor_role(payload: &Value) -> String {
    let explicit = first_text(
        payload,
        &["actor_role", "role", "access_role", "workspace_role"],
    );
    if !explicit.is_empty() {
        return explicit.to_ascii_lowercase();
    }
    if bool_field(payload, "is_owner") {
        return "owner".to_string();
    }
    if bool_field(payload, "is_admin") {
        return "admin".to_string();
    }
    if bool_field(payload, "is_service") {
        return "service".to_string();
    }
    String::new()
}

fn is_admin_role(role: &str) -> bool {
    matches!(
        role,
        "owner" | "admin" | "workspace_admin" | "tenant_admin" | "operator" | "system" | "service"
    )
}

fn is_owner_role(role: &str) -> bool {
    matches!(role, "owner" | "tenant_admin" | "system" | "service")
}

fn pagination(payload: &Value) -> Value {
    let requested_limit = numeric_field(payload, "limit").unwrap_or(50);
    let requested_offset = numeric_field(payload, "offset").unwrap_or(0);
    let max_limit = numeric_field(payload, "max_limit").unwrap_or(500);
    let hard_limit = max_limit.clamp(1, 500);
    let limit = requested_limit.clamp(1, hard_limit);
    json!({
        "limit": limit,
        "offset": requested_offset.min(100_000),
    })
}

fn text(payload: &Value, key: &str) -> String {
    payload
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string()
}

fn first_text(payload: &Value, keys: &[&str]) -> String {
    for key in keys {
        let value = text(payload, key);
        if !value.is_empty() {
            return value;
        }
    }
    String::new()
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

fn numeric_field(payload: &Value, key: &str) -> Option<u64> {
    match payload.get(key) {
        Some(Value::Number(value)) => value.as_u64(),
        Some(Value::String(value)) => value.trim().parse::<u64>().ok(),
        _ => None,
    }
}

#[cfg(test)]
mod deployed_data_kernel_boundary_tests {
    use super::deployed_data_decision_command;

    #[test]
    fn analytics_detail_requires_admin_role() {
        let decision = deployed_data_decision_command(&serde_json::json!({
            "operation": "analytics_detail",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "deployed_agent_id": "dagent_1",
            "actor_role": "viewer"
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("block")
        );
        assert_eq!(
            decision.get("reason").and_then(|value| value.as_str()),
            Some("deployed_agent_admin_required")
        );
    }

    #[test]
    fn sensitive_data_requires_owner_or_approval() {
        let decision = deployed_data_decision_command(&serde_json::json!({
            "operation": "conversation_detail",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "deployed_agent_id": "dagent_1",
            "session_id": "session-1",
            "actor_role": "admin",
            "include_raw_payload": true,
            "sensitive_access_approved": false
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("require_approval")
        );
        assert_eq!(
            decision
                .get("approval_required")
                .and_then(|value| value.as_bool()),
            Some(true)
        );
    }
}

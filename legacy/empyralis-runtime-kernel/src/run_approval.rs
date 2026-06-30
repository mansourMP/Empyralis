use serde_json::{json, Value};

const RESOLVED_APPROVAL_STATUSES: &[&str] = &[
    "decision_submitted",
    "resolved",
    "approved",
    "rejected",
    "denied",
    "expired",
    "cancelled",
    "canceled",
    "dismissed",
    "completed",
    "executed",
    "failed",
];

pub fn run_approval_decision_command(payload: &Value) -> Value {
    let operation = normalize_operation(&text_any(payload, &["operation", "action"]));
    if operation.is_empty() {
        return block("run_approval_operation_missing", "unknown");
    }
    if !known_operation(&operation) {
        return block("run_approval_operation_unknown", &operation);
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
        return block("run_approval_workspace_access_denied", &operation);
    }
    if explicit_false(payload, "approvals_enabled") {
        return block("run_approvals_not_in_plan", &operation);
    }

    let role = actor_role(payload);
    match operation.as_str() {
        "list_pending" => list_pending_decision(payload, &tenant_id, &workspace_id, &role),
        "filter_pending_item" => {
            filter_pending_item_decision(payload, &tenant_id, &workspace_id, &role)
        }
        "get_detail" => get_detail_decision(payload, &tenant_id, &workspace_id, &role),
        "create_request" => create_request_decision(payload, &tenant_id, &workspace_id, &role),
        "submit_decision" => submit_decision(payload, &tenant_id, &workspace_id, &role),
        "resolve_approval" => resolve_approval_decision(payload, &tenant_id, &workspace_id, &role),
        "record_resolution" => {
            record_resolution_decision(payload, &tenant_id, &workspace_id, &role)
        }
        _ => block("run_approval_operation_unknown", &operation),
    }
}

fn list_pending_decision(
    payload: &Value,
    tenant_id: &str,
    workspace_id: &str,
    role: &str,
) -> Value {
    if !can_view(role) {
        return block("run_approval_viewer_required", "list_pending");
    }
    if bool_field(payload, "current_user_required")
        && !is_privileged(role)
        && actor_user_id(payload).is_empty()
    {
        return block("run_approval_authenticated_user_required", "list_pending");
    }
    let limit = numeric_any(payload, &["limit"])
        .unwrap_or(100)
        .clamp(1, 300);
    allow(
        "run_approval_list_allowed",
        "list_pending",
        "list_pending_approvals",
        tenant_id,
        workspace_id,
        "",
        "",
        role,
        true,
        "standard",
    )
    .with_extra("limit", json!(limit))
    .with_extra("owner_filter_required", json!(!is_privileged(role)))
}

fn filter_pending_item_decision(
    payload: &Value,
    tenant_id: &str,
    workspace_id: &str,
    role: &str,
) -> Value {
    if !can_view(role) {
        return block("run_approval_viewer_required", "filter_pending_item");
    }
    let approval_id = text_any(payload, &["approval_id", "id"]);
    let run_id = text_any(payload, &["run_id"]);
    if approval_id.is_empty() || run_id.is_empty() {
        return block("run_approval_item_identity_missing", "filter_pending_item");
    }
    let status = normalize_status(&text_any(payload, &["approval_status", "status"]));
    if approval_resolved(&status)
        || bool_field(payload, "workspace_not_allowed")
        || (!is_privileged(role) && !approval_owned_by_actor(payload))
    {
        return allow(
            "run_approval_item_filtered",
            "filter_pending_item",
            "skip_approval_item",
            tenant_id,
            workspace_id,
            &approval_id,
            &run_id,
            role,
            true,
            "standard",
        )
        .with_extra("include_item", json!(false));
    }
    allow(
        "run_approval_item_included",
        "filter_pending_item",
        "include_approval_item",
        tenant_id,
        workspace_id,
        &approval_id,
        &run_id,
        role,
        true,
        "standard",
    )
    .with_extra("include_item", json!(true))
}

fn get_detail_decision(payload: &Value, tenant_id: &str, workspace_id: &str, role: &str) -> Value {
    if !can_view(role) {
        return block("run_approval_viewer_required", "get_detail");
    }
    let approval_id = text_any(payload, &["approval_id", "id"]);
    if approval_id.is_empty() {
        return block("approval_id_missing", "get_detail");
    }
    if bool_field(payload, "approval_missing") {
        return block("approval_not_found", "get_detail");
    }
    if bool_field(payload, "run_owner_access_granted")
        || is_privileged(role)
        || approval_owned_by_actor(payload)
    {
        return allow(
            "run_approval_detail_allowed",
            "get_detail",
            "build_approval_detail_response",
            tenant_id,
            workspace_id,
            &approval_id,
            &text_any(payload, &["run_id"]),
            role,
            true,
            "standard",
        );
    }
    block("run_approval_owned_by_another_user", "get_detail")
}

fn create_request_decision(
    payload: &Value,
    tenant_id: &str,
    workspace_id: &str,
    role: &str,
) -> Value {
    if !can_create(role) {
        return block("run_approval_create_requires_runtime", "create_request");
    }
    let run_id = text_any(payload, &["run_id"]);
    if run_id.is_empty() {
        return block("run_id_missing", "create_request");
    }
    if text_any(payload, &["prompt", "reason", "summary"]).is_empty() {
        return block("run_approval_prompt_missing", "create_request");
    }
    allow(
        "run_approval_create_allowed",
        "create_request",
        "create_or_update_approval_request",
        tenant_id,
        workspace_id,
        &text_any(payload, &["approval_id", "id"]),
        &run_id,
        role,
        false,
        "security",
    )
    .with_extra("scope", json!(defaulted_text(payload, &["scope"], "once")))
    .with_extra("reusable", json!(bool_field(payload, "reusable")))
}

fn submit_decision(payload: &Value, tenant_id: &str, workspace_id: &str, role: &str) -> Value {
    if !can_resolve(role) && !approval_owned_by_actor(payload) {
        return block("run_approval_resolve_not_authorized", "submit_decision");
    }
    approval_resolution_common(
        payload,
        tenant_id,
        workspace_id,
        role,
        "submit_decision",
        "submit_run_decision",
    )
}

fn resolve_approval_decision(
    payload: &Value,
    tenant_id: &str,
    workspace_id: &str,
    role: &str,
) -> Value {
    if !can_resolve(role) && !approval_owned_by_actor(payload) {
        return block("run_approval_resolve_not_authorized", "resolve_approval");
    }
    approval_resolution_common(
        payload,
        tenant_id,
        workspace_id,
        role,
        "resolve_approval",
        "resolve_run_approval",
    )
}

fn record_resolution_decision(
    payload: &Value,
    tenant_id: &str,
    workspace_id: &str,
    role: &str,
) -> Value {
    if !can_create(role) {
        return block("run_approval_record_requires_runtime", "record_resolution");
    }
    approval_resolution_common(
        payload,
        tenant_id,
        workspace_id,
        role,
        "record_resolution",
        "record_approval_resolution",
    )
}

fn approval_resolution_common(
    payload: &Value,
    tenant_id: &str,
    workspace_id: &str,
    role: &str,
    operation: &str,
    next_action: &str,
) -> Value {
    let approval_id = text_any(payload, &["approval_id", "id"]);
    if approval_id.is_empty() {
        return block("approval_id_missing", operation);
    }
    let expected_approval_id = text_any(
        payload,
        &[
            "expected_approval_id",
            "current_approval_id",
            "pending_approval_id",
        ],
    );
    if !expected_approval_id.is_empty() && expected_approval_id != approval_id {
        return block("run_approval_pending_mismatch", operation);
    }
    let run_id = text_any(payload, &["run_id"]);
    if run_id.is_empty() {
        return block("run_id_missing", operation);
    }
    let status = normalize_status(&text_any(payload, &["approval_status", "status"]));
    if approval_resolved(&status) {
        return block("run_approval_already_resolved", operation);
    }
    if bool_field(payload, "approval_expired") {
        return block("run_approval_expired", operation);
    }
    let decision = normalize_decision(&text_any(payload, &["decision", "resolution"]));
    if !matches!(decision.as_str(), "approved" | "rejected" | "escalated") {
        return block("run_approval_decision_invalid", operation);
    }
    allow(
        "run_approval_resolution_allowed",
        operation,
        next_action,
        tenant_id,
        workspace_id,
        &approval_id,
        &run_id,
        role,
        false,
        "security",
    )
    .with_extra("resolution", json!(decision))
}

fn allow(
    reason: &str,
    operation: &str,
    next_action: &str,
    tenant_id: &str,
    workspace_id: &str,
    approval_id: &str,
    run_id: &str,
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
        "approval_id": approval_id,
        "run_id": run_id,
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
        "list" | "pending" | "list_approvals" => "list_pending".to_string(),
        "filter" | "filter_item" => "filter_pending_item".to_string(),
        "detail" | "get" | "get_approval" => "get_detail".to_string(),
        "create" | "ensure_request" | "approval_request" => "create_request".to_string(),
        "submit" | "submit_run_decision" => "submit_decision".to_string(),
        "resolve" | "resolve_run_approval" => "resolve_approval".to_string(),
        "record" | "record_approval_resolution" => "record_resolution".to_string(),
        other => other.to_string(),
    }
}

fn known_operation(operation: &str) -> bool {
    matches!(
        operation,
        "list_pending"
            | "filter_pending_item"
            | "get_detail"
            | "create_request"
            | "submit_decision"
            | "resolve_approval"
            | "record_resolution"
    )
}

fn normalize_status(raw: &str) -> String {
    match normalize_token(raw).as_str() {
        "" => "requested".to_string(),
        "canceled" => "cancelled".to_string(),
        "reject" | "rejected" | "deny" | "denied" => "rejected".to_string(),
        "approve" | "approved" => "approved".to_string(),
        other => other.to_string(),
    }
}

fn normalize_decision(raw: &str) -> String {
    match normalize_token(raw).as_str() {
        "allow" | "approve" | "approved" | "yes" | "y" | "proceed" | "continue" | "ok" => {
            "approved".to_string()
        }
        "escalate" | "escalated" => "escalated".to_string(),
        "block" | "deny" | "denied" | "reject" | "rejected" | "no" | "n" | "stop" | "cancel"
        | "cancelled" | "canceled" => "rejected".to_string(),
        other => other.to_string(),
    }
}

fn approval_resolved(status: &str) -> bool {
    RESOLVED_APPROVAL_STATUSES.contains(&status)
}

fn actor_role(payload: &Value) -> String {
    let role = text_any(payload, &["actor_role", "user_role", "role"]);
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

fn can_create(role: &str) -> bool {
    matches!(role, "runtime" | "system" | "service" | "admin" | "owner")
}

fn can_resolve(role: &str) -> bool {
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

fn approval_owned_by_actor(payload: &Value) -> bool {
    let actor_user_id = actor_user_id(payload);
    let owner_user_id = text_any(payload, &["owner_user_id", "approval_owner_user_id"]);
    if !actor_user_id.is_empty() && !owner_user_id.is_empty() && actor_user_id == owner_user_id {
        return true;
    }
    let actor_email = text_any(payload, &["actor_email", "user_email"]).to_ascii_lowercase();
    let owner_email =
        text_any(payload, &["owner_email", "approval_owner_email"]).to_ascii_lowercase();
    !actor_email.is_empty() && !owner_email.is_empty() && actor_email == owner_email
}

fn actor_user_id(payload: &Value) -> String {
    text_any(payload, &["actor_user_id", "user_id", "current_user_id"])
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

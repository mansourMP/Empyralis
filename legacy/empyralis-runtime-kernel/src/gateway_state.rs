use serde_json::{json, Value};

const ACTIVE_REGISTRATION_STATUSES: &[&str] = &["active", "connected", "ready"];
const ACTIVE_SESSION_STATUSES: &[&str] = &["pending", "active", "connected"];
const TERMINAL_APPROVAL_STATUSES: &[&str] =
    &["approved", "denied", "expired", "executed", "failed"];

pub fn gateway_state_decision_command(payload: &Value) -> Value {
    let operation = normalize_operation(&text_any(payload, &["operation", "action"]));
    if operation.is_empty() {
        return block("gateway_state_operation_missing", "unknown");
    }
    if !known_operation(&operation) {
        return block("gateway_state_operation_unknown", &operation);
    }

    if bool_field(payload, "tenant_disabled") {
        return block("gateway_state_tenant_disabled", &operation);
    }
    if bool_field(payload, "workspace_disabled") {
        return block("gateway_state_workspace_disabled", &operation);
    }
    if bool_field(payload, "repository_read_only") && mutates_repository(&operation) {
        return block("gateway_state_repository_read_only", &operation);
    }

    match operation.as_str() {
        "create_pairing_intent" => create_pairing_intent_decision(payload),
        "expire_pairing_intent" => expire_pairing_intent_decision(payload),
        "register_gateway" => register_gateway_decision(payload),
        "issue_session" => issue_session_decision(payload),
        "validate_session" => validate_session_decision(payload),
        "mark_session_connected" => mark_session_connected_decision(payload),
        "mark_session_disconnected" => session_write_decision(
            payload,
            "mark_session_disconnected",
            "mark_gateway_session_disconnected",
            false,
        ),
        "touch_session" => touch_session_decision(payload),
        "rotate_token" => {
            admin_registration_decision(payload, "rotate_token", "rotate_gateway_token")
        }
        "revoke_registration" => revoke_registration_decision(payload),
        "update_registration_state" => update_registration_state_decision(payload),
        "record_event" => record_event_decision(payload),
        "create_approval" => create_approval_decision(payload),
        "resolve_approval" => resolve_approval_decision(payload),
        "update_browser_session" => update_browser_session_decision(payload),
        "summarize_outbox" => {
            registration_read_decision(payload, "summarize_outbox", "summarize_gateway_outbox")
        }
        "sweep_stale_sessions" => sweep_stale_sessions_decision(payload),
        _ => block("gateway_state_operation_unknown", &operation),
    }
}

fn create_pairing_intent_decision(payload: &Value) -> Value {
    let scope = scope(payload);
    if scope.tenant_id.is_empty() || scope.workspace_id.is_empty() || scope.user_id.is_empty() {
        return block("gateway_pairing_scope_missing", "create_pairing_intent");
    }
    if !can_write(&actor_role(payload)) {
        return block(
            "gateway_pairing_actor_cannot_create",
            "create_pairing_intent",
        );
    }
    let ttl_seconds = numeric_any(payload, &["ttl_seconds", "ttl"]).unwrap_or(600);
    if ttl_seconds == 0 || ttl_seconds > 86_400 {
        return block("gateway_pairing_ttl_invalid", "create_pairing_intent");
    }
    if bool_field(payload, "pending_limit_exceeded") {
        return require_approval(
            "gateway_pairing_pending_limit_exceeded",
            "create_pairing_intent",
            "request_pairing_limit_override",
            &scope,
            false,
            "security",
        );
    }
    allow(
        "gateway_pairing_create_allowed",
        "create_pairing_intent",
        "create_pairing_intent",
        &scope,
        false,
        "security",
    )
    .with_extra("ttl_seconds", json!(ttl_seconds.min(86_400)))
}

fn expire_pairing_intent_decision(payload: &Value) -> Value {
    let scope = scope(payload);
    if scope.tenant_id.is_empty() || scope.workspace_id.is_empty() || scope.user_id.is_empty() {
        return block("gateway_pairing_scope_missing", "expire_pairing_intent");
    }
    if !can_write(&actor_role(payload)) {
        return block(
            "gateway_pairing_actor_cannot_expire",
            "expire_pairing_intent",
        );
    }
    let pairing_id = text_any(payload, &["pairing_id"]);
    if pairing_id.is_empty() && !bool_field(payload, "bulk_expire") {
        return block("gateway_pairing_id_missing", "expire_pairing_intent");
    }
    let pairing_status = normalize_status(&text_any(
        payload,
        &["pairing_status", "current_status", "status"],
    ));
    if !pairing_status.is_empty() && pairing_status != "pending" {
        return block("gateway_pairing_not_pending", "expire_pairing_intent");
    }
    allow(
        "gateway_pairing_expire_allowed",
        "expire_pairing_intent",
        "mark_pairing_intent_expired",
        &scope,
        false,
        "security",
    )
}

fn register_gateway_decision(payload: &Value) -> Value {
    let scope = scope(payload);
    if scope.gateway_id.is_empty() && text_any(payload, &["device_id"]).is_empty() {
        return block("gateway_registration_identity_missing", "register_gateway");
    }
    if !bool_field(payload, "token_valid") {
        return block("gateway_pairing_token_invalid", "register_gateway");
    }
    let pairing_status = normalize_token(&text_any(payload, &["pairing_status", "status"]));
    if !pairing_status.is_empty() && pairing_status != "pending" {
        return block("gateway_pairing_not_pending", "register_gateway");
    }
    if bool_field(payload, "pairing_expired") {
        return block("gateway_pairing_expired", "register_gateway");
    }
    allow(
        "gateway_registration_allowed",
        "register_gateway",
        "consume_pairing_and_register_gateway",
        &scope,
        false,
        "security",
    )
}

fn issue_session_decision(payload: &Value) -> Value {
    let scope = match require_gateway_scope(payload, "issue_session") {
        Ok(scope) => scope,
        Err(response) => return response,
    };
    let status = normalize_status(&text_any(payload, &["registration_status", "status"]));
    if !status.is_empty() && !ACTIVE_REGISTRATION_STATUSES.contains(&status.as_str()) {
        return block("gateway_registration_not_active", "issue_session");
    }
    let trust_state = normalize_token(&text_any(payload, &["device_trust_state", "trust_state"]));
    if matches!(trust_state.as_str(), "revoked" | "blocked" | "quarantined") {
        return block("gateway_device_trust_revoked", "issue_session");
    }
    let ttl_seconds = numeric_any(payload, &["ttl_seconds", "session_ttl_seconds"]).unwrap_or(3600);
    if ttl_seconds == 0 || ttl_seconds > 86_400 {
        return block("gateway_session_ttl_invalid", "issue_session");
    }
    allow(
        "gateway_session_issue_allowed",
        "issue_session",
        "issue_gateway_session",
        &scope,
        false,
        "security",
    )
    .with_extra("ttl_seconds", json!(ttl_seconds.min(86_400)))
}

fn validate_session_decision(payload: &Value) -> Value {
    let scope = match require_gateway_scope(payload, "validate_session") {
        Ok(scope) => scope,
        Err(response) => return response,
    };
    if scope.session_id.is_empty() {
        return block("gateway_session_id_missing", "validate_session");
    }
    if !bool_field(payload, "token_valid") {
        return block("gateway_session_token_invalid", "validate_session");
    }
    let status = normalize_status(&text_any(payload, &["session_status", "status"]));
    if !status.is_empty() && !ACTIVE_SESSION_STATUSES.contains(&status.as_str()) {
        return block("gateway_session_not_active", "validate_session");
    }
    if bool_field(payload, "session_expired") {
        return block("gateway_session_expired", "validate_session");
    }
    if bool_field(payload, "scope_mismatch") {
        return block("gateway_session_scope_mismatch", "validate_session");
    }
    allow(
        "gateway_session_validate_allowed",
        "validate_session",
        "validate_gateway_session",
        &scope,
        true,
        "standard",
    )
}

fn mark_session_connected_decision(payload: &Value) -> Value {
    let scope = match require_gateway_scope(payload, "mark_session_connected") {
        Ok(scope) => scope,
        Err(response) => return response,
    };
    if scope.session_id.is_empty() {
        return block("gateway_session_id_missing", "mark_session_connected");
    }
    let status = normalize_status(&text_any(payload, &["session_status", "status"]));
    if !status.is_empty() && !ACTIVE_SESSION_STATUSES.contains(&status.as_str()) {
        return block("gateway_session_not_connectable", "mark_session_connected");
    }
    if bool_field(payload, "binding_invalid") {
        return block("gateway_binding_invalid", "mark_session_connected");
    }
    allow(
        "gateway_session_connect_allowed",
        "mark_session_connected",
        "mark_gateway_session_connected",
        &scope,
        false,
        "security",
    )
}

fn session_write_decision(
    payload: &Value,
    operation: &str,
    next_action: &str,
    cacheable: bool,
) -> Value {
    let scope = match require_gateway_scope(payload, operation) {
        Ok(scope) => scope,
        Err(response) => return response,
    };
    if scope.session_id.is_empty() {
        return block("gateway_session_id_missing", operation);
    }
    allow(
        "gateway_session_write_allowed",
        operation,
        next_action,
        &scope,
        cacheable,
        "standard",
    )
}

fn touch_session_decision(payload: &Value) -> Value {
    let scope = match require_gateway_scope(payload, "touch_session") {
        Ok(scope) => scope,
        Err(response) => return response,
    };
    if scope.session_id.is_empty() {
        return block("gateway_session_id_missing", "touch_session");
    }
    let seq = numeric_any(payload, &["seq", "last_seq"]);
    let ack = numeric_any(payload, &["ack", "last_ack"]);
    let previous_seq = numeric_any(payload, &["previous_seq", "stored_seq"]);
    let previous_ack = numeric_any(payload, &["previous_ack", "stored_ack"]);
    if let (Some(next), Some(previous)) = (seq, previous_seq) {
        if next < previous {
            return block("gateway_session_seq_regressed", "touch_session");
        }
    }
    if let (Some(next), Some(previous)) = (ack, previous_ack) {
        if next < previous {
            return block("gateway_session_ack_regressed", "touch_session");
        }
    }
    allow(
        "gateway_session_touch_allowed",
        "touch_session",
        "touch_gateway_session",
        &scope,
        false,
        "standard",
    )
}

fn admin_registration_decision(payload: &Value, operation: &str, next_action: &str) -> Value {
    let scope = match require_gateway_scope(payload, operation) {
        Ok(scope) => scope,
        Err(response) => return response,
    };
    if !can_admin(&actor_role(payload)) {
        return block("gateway_registration_admin_required", operation);
    }
    let status = normalize_status(&text_any(payload, &["registration_status", "status"]));
    if matches!(status.as_str(), "revoked" | "deleted") {
        return block("gateway_registration_terminal", operation);
    }
    allow(
        "gateway_registration_admin_write_allowed",
        operation,
        next_action,
        &scope,
        false,
        "security",
    )
}

fn revoke_registration_decision(payload: &Value) -> Value {
    let scope = match require_gateway_scope(payload, "revoke_registration") {
        Ok(scope) => scope,
        Err(response) => return response,
    };
    if !can_admin(&actor_role(payload)) {
        return block("gateway_registration_admin_required", "revoke_registration");
    }
    if text_any(payload, &["reason", "revoked_reason"]).is_empty() {
        return require_approval(
            "gateway_revoke_reason_required",
            "revoke_registration",
            "request_gateway_revoke_confirmation",
            &scope,
            false,
            "security",
        );
    }
    allow(
        "gateway_registration_revoke_allowed",
        "revoke_registration",
        "revoke_gateway_registration",
        &scope,
        false,
        "security",
    )
}

fn update_registration_state_decision(payload: &Value) -> Value {
    let scope = match require_gateway_scope(payload, "update_registration_state") {
        Ok(scope) => scope,
        Err(response) => return response,
    };
    if !can_admin(&actor_role(payload)) {
        return block(
            "gateway_registration_admin_required",
            "update_registration_state",
        );
    }
    let next_status = normalize_status(&text_any(
        payload,
        &["next_status", "target_status", "status"],
    ));
    if !matches!(
        next_status.as_str(),
        "active" | "suspended" | "revoked" | "blocked"
    ) {
        return block(
            "gateway_registration_status_invalid",
            "update_registration_state",
        );
    }
    if matches!(next_status.as_str(), "revoked" | "blocked")
        && text_any(payload, &["reason", "note"]).is_empty()
    {
        return require_approval(
            "gateway_destructive_state_change_requires_reason",
            "update_registration_state",
            "request_gateway_state_change_confirmation",
            &scope,
            false,
            "security",
        );
    }
    allow(
        "gateway_registration_state_update_allowed",
        "update_registration_state",
        "update_gateway_registration_state",
        &scope,
        false,
        "security",
    )
    .with_extra("next_status", json!(next_status))
}

fn record_event_decision(payload: &Value) -> Value {
    let scope = match require_gateway_scope(payload, "record_event") {
        Ok(scope) => scope,
        Err(response) => return response,
    };
    let direction = normalize_token(&text_any(payload, &["direction"]));
    let frame_kind = normalize_token(&text_any(payload, &["frame_kind", "kind"]));
    let message_type = text_any(payload, &["message_type", "type"]);
    if direction.is_empty() || frame_kind.is_empty() || message_type.is_empty() {
        return block("gateway_event_shape_missing", "record_event");
    }
    let payload_size =
        numeric_any(payload, &["payload_size_bytes", "frame_size_bytes"]).unwrap_or(0);
    if payload_size > 16 * 1024 * 1024 {
        return block("gateway_event_payload_too_large", "record_event");
    }
    allow(
        "gateway_event_record_allowed",
        "record_event",
        "record_gateway_event",
        &scope,
        false,
        "standard",
    )
}

fn create_approval_decision(payload: &Value) -> Value {
    let scope = match require_gateway_scope(payload, "create_approval") {
        Ok(scope) => scope,
        Err(response) => return response,
    };
    if text_any(payload, &["run_id"]).is_empty() {
        return block("gateway_approval_run_id_missing", "create_approval");
    }
    if text_any(payload, &["capability_id", "capability"]).is_empty() {
        return block("gateway_approval_capability_missing", "create_approval");
    }
    allow(
        "gateway_approval_create_allowed",
        "create_approval",
        "create_gateway_action_approval",
        &scope,
        false,
        "security",
    )
}

fn resolve_approval_decision(payload: &Value) -> Value {
    let scope = match require_gateway_scope(payload, "resolve_approval") {
        Ok(scope) => scope,
        Err(response) => return response,
    };
    if text_any(payload, &["approval_id"]).is_empty() {
        return block("gateway_approval_id_missing", "resolve_approval");
    }
    if !can_admin(&actor_role(payload)) {
        return block("gateway_approval_admin_required", "resolve_approval");
    }
    let status = normalize_status(&text_any(payload, &["approval_status", "status"]));
    if TERMINAL_APPROVAL_STATUSES.contains(&status.as_str()) {
        return block("gateway_approval_already_terminal", "resolve_approval");
    }
    let decision = normalize_token(&text_any(payload, &["decision"]));
    if !matches!(decision.as_str(), "approved" | "denied" | "allow" | "block") {
        return block("gateway_approval_decision_invalid", "resolve_approval");
    }
    allow(
        "gateway_approval_resolve_allowed",
        "resolve_approval",
        "resolve_gateway_action_approval_atomic",
        &scope,
        false,
        "security",
    )
    .with_extra("approval_decision", json!(decision))
}

fn update_browser_session_decision(payload: &Value) -> Value {
    let scope = match require_gateway_scope(payload, "update_browser_session") {
        Ok(scope) => scope,
        Err(response) => return response,
    };
    if text_any(payload, &["browser_session_id"]).is_empty()
        && text_any(payload, &["run_id"]).is_empty()
    {
        return block(
            "gateway_browser_session_identity_missing",
            "update_browser_session",
        );
    }
    let status = normalize_status(&text_any(payload, &["browser_status", "status"]));
    if !status.is_empty()
        && !matches!(
            status.as_str(),
            "active"
                | "paused"
                | "waiting_for_input"
                | "fallback_ready"
                | "attached"
                | "not_attached"
                | "attach_required"
                | "attach_failed"
                | "interrupted"
                | "completed"
                | "failed"
                | "cancelled"
        )
    {
        return block(
            "gateway_browser_session_status_invalid",
            "update_browser_session",
        );
    }
    if bool_field(payload, "manual_takeover") && !bool_field(payload, "takeover_confirmed") {
        return require_approval(
            "gateway_browser_takeover_requires_confirmation",
            "update_browser_session",
            "request_browser_takeover_confirmation",
            &scope,
            false,
            "security",
        );
    }
    allow(
        "gateway_browser_session_update_allowed",
        "update_browser_session",
        "upsert_gateway_browser_session",
        &scope,
        false,
        "security",
    )
}

fn registration_read_decision(payload: &Value, operation: &str, next_action: &str) -> Value {
    let scope = match require_gateway_scope(payload, operation) {
        Ok(scope) => scope,
        Err(response) => return response,
    };
    allow(
        "gateway_registration_read_allowed",
        operation,
        next_action,
        &scope,
        true,
        "standard",
    )
}

fn sweep_stale_sessions_decision(payload: &Value) -> Value {
    let scope = scope(payload);
    if !can_admin(&actor_role(payload)) {
        return block("gateway_sweep_admin_required", "sweep_stale_sessions");
    }
    let stale_count = numeric_any(payload, &["stale_count", "candidate_count"]).unwrap_or(0);
    allow(
        "gateway_stale_session_sweep_allowed",
        "sweep_stale_sessions",
        if stale_count == 0 {
            "noop"
        } else {
            "sweep_stale_gateway_sessions"
        },
        &scope,
        stale_count == 0,
        "standard",
    )
    .with_extra("stale_count", json!(stale_count))
}

fn require_gateway_scope(payload: &Value, operation: &str) -> Result<GatewayScope, Value> {
    let scope = scope(payload);
    if scope.gateway_id.is_empty() {
        return Err(block("gateway_id_missing", operation));
    }
    if scope.tenant_id.is_empty() {
        return Err(block("tenant_id_missing", operation));
    }
    if scope.workspace_id.is_empty() {
        return Err(block("workspace_id_missing", operation));
    }
    Ok(scope)
}

fn allow(
    reason: &str,
    operation: &str,
    next_action: &str,
    scope: &GatewayScope,
    cacheable: bool,
    audit_visibility: &str,
) -> Value {
    json!({
        "ok": true,
        "decision": "allow",
        "reason": reason,
        "operation": operation,
        "next_action": next_action,
        "tenant_id": scope.tenant_id.as_str(),
        "workspace_id": scope.workspace_id.as_str(),
        "user_id": scope.user_id.as_str(),
        "device_id": scope.device_id.as_str(),
        "gateway_id": scope.gateway_id.as_str(),
        "session_id": scope.session_id.as_str(),
        "approval_required": false,
        "cacheable": cacheable,
        "audit_visibility": audit_visibility,
    })
}

fn require_approval(
    reason: &str,
    operation: &str,
    next_action: &str,
    scope: &GatewayScope,
    cacheable: bool,
    audit_visibility: &str,
) -> Value {
    json!({
        "ok": true,
        "decision": "require_approval",
        "reason": reason,
        "operation": operation,
        "next_action": next_action,
        "tenant_id": scope.tenant_id.as_str(),
        "workspace_id": scope.workspace_id.as_str(),
        "gateway_id": scope.gateway_id.as_str(),
        "session_id": scope.session_id.as_str(),
        "approval_required": true,
        "approval_scope": format!(
            "gateway-state:{operation}:{}:{}",
            scope.workspace_id.as_str(),
            scope.gateway_id.as_str()
        ),
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

#[derive(Clone)]
struct GatewayScope {
    tenant_id: String,
    workspace_id: String,
    user_id: String,
    device_id: String,
    gateway_id: String,
    session_id: String,
}

fn scope(payload: &Value) -> GatewayScope {
    GatewayScope {
        tenant_id: text_any(payload, &["tenant_id", "owner_tenant_id"]),
        workspace_id: text_any(payload, &["workspace_id", "owner_workspace_id"]),
        user_id: text_any(payload, &["user_id", "owner_user_id"]),
        device_id: text_any(payload, &["device_id"]),
        gateway_id: text_any(payload, &["gateway_id", "runtime_id"]),
        session_id: text_any(payload, &["session_id", "gateway_session_id"]),
    }
}

fn normalize_operation(raw: &str) -> String {
    match normalize_token(raw).as_str() {
        "pairing_create" | "create_pairing" => "create_pairing_intent".to_string(),
        "pairing_expire" | "expire_pairing" => "expire_pairing_intent".to_string(),
        "registration_create" | "consume_pairing" => "register_gateway".to_string(),
        "create_session" | "session_issue" => "issue_session".to_string(),
        "session_validate" => "validate_session".to_string(),
        "connect_session" | "session_connected" => "mark_session_connected".to_string(),
        "disconnect_session" | "session_disconnected" => "mark_session_disconnected".to_string(),
        "session_touch" | "heartbeat_session" => "touch_session".to_string(),
        "rotate_gateway_token" => "rotate_token".to_string(),
        "revoke_gateway" => "revoke_registration".to_string(),
        "registration_state" => "update_registration_state".to_string(),
        "gateway_event" => "record_event".to_string(),
        "approval_create" => "create_approval".to_string(),
        "approval_resolve" => "resolve_approval".to_string(),
        "browser_session" | "upsert_browser_session" => "update_browser_session".to_string(),
        "outbox_summary" => "summarize_outbox".to_string(),
        "sweep_sessions" => "sweep_stale_sessions".to_string(),
        other => other.to_string(),
    }
}

fn known_operation(operation: &str) -> bool {
    matches!(
        operation,
        "create_pairing_intent"
            | "expire_pairing_intent"
            | "register_gateway"
            | "issue_session"
            | "validate_session"
            | "mark_session_connected"
            | "mark_session_disconnected"
            | "touch_session"
            | "rotate_token"
            | "revoke_registration"
            | "update_registration_state"
            | "record_event"
            | "create_approval"
            | "resolve_approval"
            | "update_browser_session"
            | "summarize_outbox"
            | "sweep_stale_sessions"
    )
}

fn mutates_repository(operation: &str) -> bool {
    !matches!(operation, "validate_session" | "summarize_outbox")
}

fn actor_role(payload: &Value) -> String {
    let role = text_any(payload, &["actor_role", "role", "access_role"]);
    if !role.is_empty() {
        return normalize_token(&role);
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
    "member".to_string()
}

fn can_write(role: &str) -> bool {
    matches!(
        role,
        "owner"
            | "admin"
            | "workspace_admin"
            | "operator"
            | "system"
            | "service"
            | "member"
            | "user"
    )
}

fn can_admin(role: &str) -> bool {
    matches!(
        role,
        "owner" | "admin" | "workspace_admin" | "tenant_admin" | "operator" | "system" | "service"
    )
}

fn normalize_status(raw: &str) -> String {
    match normalize_token(raw).as_str() {
        "connected" => "active".to_string(),
        "revoked" => "revoked".to_string(),
        "blocked" => "blocked".to_string(),
        other => other.to_string(),
    }
}

fn normalize_token(raw: &str) -> String {
    raw.trim().to_ascii_lowercase().replace('-', "_")
}

fn text_any(payload: &Value, keys: &[&str]) -> String {
    for key in keys {
        if let Some(value) = payload.get(key).and_then(Value::as_str) {
            let trimmed = value.trim();
            if !trimmed.is_empty() {
                return trimmed.to_string();
            }
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

fn numeric_any(payload: &Value, keys: &[&str]) -> Option<u64> {
    for key in keys {
        match payload.get(key) {
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn pairing_expiry_allows_pending_pairing() {
        let response = gateway_state_decision_command(&json!({
            "operation": "expire_pairing_intent",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "user_id": "user-1",
            "actor_role": "system",
            "pairing_id": "pairing-1",
            "pairing_status": "pending"
        }));

        assert_eq!(response["decision"].as_str(), Some("allow"));
        assert_eq!(
            response["operation"].as_str(),
            Some("expire_pairing_intent")
        );
        assert_eq!(
            response["next_action"].as_str(),
            Some("mark_pairing_intent_expired")
        );
    }

    #[test]
    fn pairing_expiry_requires_pairing_id_or_bulk_marker() {
        let response = gateway_state_decision_command(&json!({
            "operation": "expire_pairing_intent",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "user_id": "user-1",
            "actor_role": "system",
            "pairing_status": "pending"
        }));

        assert_eq!(response["decision"].as_str(), Some("block"));
        assert_eq!(
            response["reason"].as_str(),
            Some("gateway_pairing_id_missing")
        );
    }

    #[test]
    fn register_gateway_requires_valid_pairing_token() {
        let response = gateway_state_decision_command(&json!({
            "operation": "register_gateway",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "user_id": "user-1",
            "device_id": "device-1",
            "gateway_id": "gateway-1",
            "token_valid": false,
            "pairing_status": "pending"
        }));

        assert_eq!(response["decision"].as_str(), Some("block"));
        assert_eq!(
            response["reason"].as_str(),
            Some("gateway_pairing_token_invalid")
        );
    }

    #[test]
    fn browser_session_allows_confirmed_waiting_for_input_takeover() {
        let response = gateway_state_decision_command(&json!({
            "operation": "update_browser_session",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "user_id": "user-1",
            "device_id": "device-1",
            "gateway_id": "gateway-1",
            "browser_session_id": "gbsess-1",
            "run_id": "run-1",
            "status": "waiting_for_input",
            "manual_takeover": true,
            "takeover_confirmed": true,
            "actor_role": "system"
        }));

        assert_eq!(response["decision"].as_str(), Some("allow"));
        assert_eq!(
            response["next_action"].as_str(),
            Some("upsert_gateway_browser_session")
        );
    }

    #[test]
    fn browser_session_takeover_still_requires_confirmation() {
        let response = gateway_state_decision_command(&json!({
            "operation": "update_browser_session",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "user_id": "user-1",
            "device_id": "device-1",
            "gateway_id": "gateway-1",
            "browser_session_id": "gbsess-1",
            "run_id": "run-1",
            "status": "waiting_for_input",
            "manual_takeover": true,
            "takeover_confirmed": false,
            "actor_role": "system"
        }));

        assert_eq!(response["decision"].as_str(), Some("require_approval"));
        assert_eq!(
            response["reason"].as_str(),
            Some("gateway_browser_takeover_requires_confirmation")
        );
    }

    #[test]
    fn browser_session_allows_fallback_and_attach_lifecycle_statuses() {
        for status in [
            "fallback_ready",
            "attached",
            "not_attached",
            "attach_required",
            "attach_failed",
        ] {
            let response = gateway_state_decision_command(&json!({
                "operation": "update_browser_session",
                "tenant_id": "tenant-1",
                "workspace_id": "workspace-1",
                "user_id": "user-1",
                "device_id": "device-1",
                "gateway_id": "gateway-1",
                "browser_session_id": format!("gbsess-{}", status),
                "run_id": "run-1",
                "status": status,
                "actor_role": "system"
            }));

            assert_eq!(response["decision"].as_str(), Some("allow"));
            assert_eq!(
                response["next_action"].as_str(),
                Some("upsert_gateway_browser_session")
            );
        }
    }
}

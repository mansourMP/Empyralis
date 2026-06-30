use serde_json::{json, Value};

const CONTROL_PLANE_RECORD_TYPES: &[&str] = &[
    "tenant",
    "workspace",
    "user",
    "auth_identity",
    "workspace_membership",
    "workspace_invite",
    "pilot_invite",
    "billing_account",
    "billing_subscription",
    "agent_thread",
    "deployed_agent",
    "gateway",
    "session",
    "run",
];

const TERMINAL_STATUSES: &[&str] = &["archived", "deleted", "revoked"];

pub fn control_plane_decision_command(input: &Value) -> Value {
    let operation = normalize_operation(
        string_field(input, "operation")
            .or_else(|| string_field(input, "action"))
            .or_else(|| string_field(input, "intent"))
            .as_deref()
            .unwrap_or(""),
    );
    if operation.is_empty() {
        return block("control_plane_operation_missing");
    }

    let record_type = normalize_record_type(
        string_field(input, "record_type")
            .or_else(|| string_field(input, "entity_type"))
            .or_else(|| string_field(input, "table"))
            .as_deref()
            .unwrap_or(""),
    );
    if record_type.is_empty() {
        return block("control_plane_record_type_missing");
    }
    if !CONTROL_PLANE_RECORD_TYPES.contains(&record_type.as_str()) {
        return block("control_plane_record_type_unknown");
    }

    let actor_role = normalize_actor_role(
        string_field(input, "actor_role")
            .or_else(|| string_field(input, "role"))
            .as_deref()
            .unwrap_or("member"),
    );
    let workspace_id = string_field(input, "workspace_id");
    let tenant_id = string_field(input, "tenant_id");
    let record_id = string_field(input, "record_id")
        .or_else(|| string_field(input, "id"))
        .or_else(|| string_field(input, "resource_id"));
    let current_status = normalize_status(
        string_field(input, "current_status")
            .or_else(|| string_field(input, "status"))
            .as_deref()
            .unwrap_or(""),
    );
    let next_status = normalize_status(
        string_field(input, "next_status")
            .or_else(|| string_field(input, "target_status"))
            .as_deref()
            .unwrap_or(""),
    );

    if matches!(operation.as_str(), "read" | "list" | "lookup") {
        return allow(
            "control_plane_read_allowed",
            &operation,
            &record_type,
            "read_record",
            false,
            true,
            "standard",
            tenant_id,
            workspace_id,
            record_id,
            &actor_role,
        );
    }

    if !actor_can_write(&actor_role) {
        return block("control_plane_actor_cannot_write");
    }

    if requires_workspace_scope(&record_type) && workspace_id.is_none() {
        return block("workspace_id_missing");
    }
    if requires_tenant_scope(&record_type) && tenant_id.is_none() {
        return block("tenant_id_missing");
    }
    if requires_existing_record(&operation) && record_id.is_none() {
        return block("record_id_missing");
    }
    if TERMINAL_STATUSES.contains(&current_status.as_str())
        && !matches!(operation.as_str(), "restore" | "read" | "list" | "lookup")
    {
        return block("control_plane_terminal_record_locked");
    }

    if operation == "status_transition" {
        return status_transition_decision(
            &record_type,
            &current_status,
            &next_status,
            tenant_id,
            workspace_id,
            record_id,
            &actor_role,
        );
    }

    if requires_approval(&operation, &record_type, &actor_role) {
        return require_approval(
            "control_plane_operation_requires_approval",
            &operation,
            &record_type,
            "request_owner_approval",
            tenant_id,
            workspace_id,
            record_id,
            &actor_role,
        );
    }

    let next_action = match operation.as_str() {
        "create" => "create_record",
        "upsert" => "upsert_record",
        "update" => "update_record",
        "archive" => "archive_record",
        "restore" => "restore_record",
        "lock" => "lock_record",
        "unlock" => "unlock_record",
        _ => return block("control_plane_operation_unknown"),
    };

    allow(
        "control_plane_operation_allowed",
        &operation,
        &record_type,
        next_action,
        false,
        false,
        "security",
        tenant_id,
        workspace_id,
        record_id,
        &actor_role,
    )
}

fn status_transition_decision(
    record_type: &str,
    current_status: &str,
    next_status: &str,
    tenant_id: Option<String>,
    workspace_id: Option<String>,
    record_id: Option<String>,
    actor_role: &str,
) -> Value {
    if current_status.is_empty() || next_status.is_empty() {
        return block("control_plane_status_transition_missing_status");
    }
    if current_status == next_status {
        return allow(
            "control_plane_status_transition_noop",
            "status_transition",
            record_type,
            "noop",
            false,
            false,
            "standard",
            tenant_id,
            workspace_id,
            record_id,
            actor_role,
        );
    }
    if !allowed_status_transition(current_status, next_status) {
        return block("control_plane_status_transition_invalid");
    }
    if matches!(next_status, "live" | "suspended" | "deleted") && actor_role != "system" {
        return require_approval(
            "control_plane_status_transition_requires_approval",
            "status_transition",
            record_type,
            "request_owner_approval",
            tenant_id,
            workspace_id,
            record_id,
            actor_role,
        );
    }
    allow(
        "control_plane_status_transition_allowed",
        "status_transition",
        record_type,
        "write_status_transition",
        false,
        false,
        "security",
        tenant_id,
        workspace_id,
        record_id,
        actor_role,
    )
}

fn allow(
    reason: &str,
    operation: &str,
    record_type: &str,
    next_action: &str,
    approval_required: bool,
    cacheable: bool,
    audit_visibility: &str,
    tenant_id: Option<String>,
    workspace_id: Option<String>,
    record_id: Option<String>,
    actor_role: &str,
) -> Value {
    response(
        "allow",
        reason,
        operation,
        record_type,
        next_action,
        approval_required,
        cacheable,
        audit_visibility,
        tenant_id,
        workspace_id,
        record_id,
        actor_role,
    )
}

fn require_approval(
    reason: &str,
    operation: &str,
    record_type: &str,
    next_action: &str,
    tenant_id: Option<String>,
    workspace_id: Option<String>,
    record_id: Option<String>,
    actor_role: &str,
) -> Value {
    response(
        "require_approval",
        reason,
        operation,
        record_type,
        next_action,
        true,
        false,
        "security",
        tenant_id,
        workspace_id,
        record_id,
        actor_role,
    )
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

fn response(
    decision: &str,
    reason: &str,
    operation: &str,
    record_type: &str,
    next_action: &str,
    approval_required: bool,
    cacheable: bool,
    audit_visibility: &str,
    tenant_id: Option<String>,
    workspace_id: Option<String>,
    record_id: Option<String>,
    actor_role: &str,
) -> Value {
    json!({
        "ok": decision != "block",
        "decision": decision,
        "reason": reason,
        "operation": operation,
        "record_type": record_type,
        "next_action": next_action,
        "approval_required": approval_required || decision == "require_approval",
        "cacheable": cacheable,
        "audit_visibility": audit_visibility,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "record_id": record_id,
        "actor_role": actor_role,
    })
}

fn normalize_operation(value: &str) -> String {
    match normalize_token(value).as_str() {
        "read" | "get" | "fetch" => "read".to_string(),
        "list" | "search" => "list".to_string(),
        "lookup" => "lookup".to_string(),
        "create" | "insert" => "create".to_string(),
        "update" | "patch" | "modify" => "update".to_string(),
        "upsert" => "upsert".to_string(),
        "archive" => "archive".to_string(),
        "restore" | "unarchive" => "restore".to_string(),
        "delete" | "destroy" | "purge" => "delete".to_string(),
        "lock" | "suspend_write" => "lock".to_string(),
        "unlock" | "resume_write" => "unlock".to_string(),
        "status" | "transition" | "status_transition" => "status_transition".to_string(),
        _ => String::new(),
    }
}

fn normalize_record_type(value: &str) -> String {
    match normalize_token(value).as_str() {
        "tenant" | "tenants" => "tenant".to_string(),
        "workspace" | "workspaces" => "workspace".to_string(),
        "user" | "users" => "user".to_string(),
        "auth_identity" | "auth_identities" | "identity" => "auth_identity".to_string(),
        "workspace_membership" | "workspace_memberships" | "membership" => {
            "workspace_membership".to_string()
        }
        "workspace_invite" | "workspace_member_invite" | "invite" => "workspace_invite".to_string(),
        "pilot_invite" => "pilot_invite".to_string(),
        "workspace_billing_account" | "billing_account" => "billing_account".to_string(),
        "workspace_billing_subscription" | "billing_subscription" | "subscription" => {
            "billing_subscription".to_string()
        }
        "agent_thread" | "agent_threads" | "thread" => "agent_thread".to_string(),
        "deployed_agent" | "deployed_agents" | "agent" => "deployed_agent".to_string(),
        "gateway" | "gateways" => "gateway".to_string(),
        "session" | "sessions" => "session".to_string(),
        "run" | "runs" => "run".to_string(),
        _ => String::new(),
    }
}

fn normalize_actor_role(value: &str) -> String {
    match normalize_token(value).as_str() {
        "system" | "service" => "system".to_string(),
        "owner" | "admin" => "owner".to_string(),
        "editor" | "maintainer" | "member" => "member".to_string(),
        "viewer" | "readonly" | "read_only" => "viewer".to_string(),
        _ => "member".to_string(),
    }
}

fn normalize_status(value: &str) -> String {
    normalize_token(value)
}

fn normalize_token(value: &str) -> String {
    value
        .trim()
        .to_ascii_lowercase()
        .replace(['-', '.', ' '], "_")
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

fn actor_can_write(actor_role: &str) -> bool {
    matches!(actor_role, "system" | "owner" | "member")
}

fn requires_workspace_scope(record_type: &str) -> bool {
    !matches!(record_type, "tenant" | "pilot_invite")
}

fn requires_tenant_scope(record_type: &str) -> bool {
    !matches!(record_type, "pilot_invite")
}

fn requires_existing_record(operation: &str) -> bool {
    matches!(
        operation,
        "update" | "archive" | "restore" | "delete" | "lock" | "unlock" | "status_transition"
    )
}

fn requires_approval(operation: &str, record_type: &str, actor_role: &str) -> bool {
    if actor_role == "system" {
        return false;
    }
    matches!(operation, "delete" | "unlock")
        || matches!(
            record_type,
            "billing_account" | "billing_subscription" | "pilot_invite"
        )
}

fn allowed_status_transition(current: &str, next: &str) -> bool {
    matches!(
        (current, next),
        ("draft", "private_test")
            | ("draft", "ready_for_review")
            | ("draft", "archived")
            | ("private_test", "draft")
            | ("private_test", "ready_for_review")
            | ("private_test", "archived")
            | ("ready_for_review", "private_test")
            | ("ready_for_review", "live")
            | ("ready_for_review", "archived")
            | ("live", "paused")
            | ("live", "suspended")
            | ("live", "archived")
            | ("paused", "live")
            | ("paused", "suspended")
            | ("paused", "archived")
            | ("suspended", "paused")
            | ("suspended", "archived")
            | ("archived", "draft")
            | ("active", "paused")
            | ("active", "suspended")
            | ("active", "archived")
            | ("paused", "active")
            | ("suspended", "active")
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blocks_missing_operation() {
        let response = control_plane_decision_command(&json!({"record_type": "workspace"}));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "control_plane_operation_missing");
    }

    #[test]
    fn allows_cacheable_read() {
        let response = control_plane_decision_command(&json!({
            "operation": "read",
            "record_type": "workspace",
            "workspace_id": "w1",
            "tenant_id": "t1"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["cacheable"], true);
    }

    #[test]
    fn blocks_write_without_workspace_scope() {
        let response = control_plane_decision_command(&json!({
            "operation": "create",
            "record_type": "deployed_agent",
            "tenant_id": "t1"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "workspace_id_missing");
    }

    #[test]
    fn requires_approval_for_billing_update() {
        let response = control_plane_decision_command(&json!({
            "operation": "update",
            "record_type": "billing_subscription",
            "workspace_id": "w1",
            "tenant_id": "t1",
            "record_id": "sub1",
            "actor_role": "owner"
        }));
        assert_eq!(response["decision"], "require_approval");
    }

    #[test]
    fn requires_approval_for_live_status_transition() {
        let response = control_plane_decision_command(&json!({
            "operation": "status_transition",
            "record_type": "deployed_agent",
            "workspace_id": "w1",
            "tenant_id": "t1",
            "record_id": "agent1",
            "current_status": "ready_for_review",
            "next_status": "live",
            "actor_role": "owner"
        }));
        assert_eq!(response["decision"], "require_approval");
    }

    #[test]
    fn blocks_invalid_status_transition() {
        let response = control_plane_decision_command(&json!({
            "operation": "status_transition",
            "record_type": "deployed_agent",
            "workspace_id": "w1",
            "tenant_id": "t1",
            "record_id": "agent1",
            "current_status": "archived",
            "next_status": "live",
            "actor_role": "system"
        }));
        assert_eq!(response["decision"], "block");
    }
}

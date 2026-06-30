use serde_json::{json, Value};

const RUN_TERMINAL_STATUSES: &[&str] = &[
    "completed",
    "succeeded",
    "failed",
    "cancelled",
    "canceled",
    "timeout",
    "timed_out",
];

pub fn run_api_decision_command(input: &Value) -> Value {
    let operation = normalize_operation(
        string_field(input, "operation")
            .or_else(|| string_field(input, "endpoint"))
            .or_else(|| string_field(input, "action"))
            .as_deref()
            .unwrap_or(""),
    );
    if operation.is_empty() {
        return block("run_api_operation_missing");
    }

    let workspace_id = string_field(input, "workspace_id");
    let tenant_id = string_field(input, "tenant_id");
    let run_id = string_field(input, "run_id").or_else(|| string_field(input, "id"));
    let user_role = normalize_user_role(
        string_field(input, "user_role")
            .or_else(|| string_field(input, "role"))
            .as_deref()
            .unwrap_or("viewer"),
    );
    let run_status = normalize_token(
        string_field(input, "run_status")
            .or_else(|| string_field(input, "status"))
            .as_deref()
            .unwrap_or(""),
    );

    if workspace_id.is_none() {
        return block("workspace_id_missing");
    }
    if tenant_id.is_none() && requires_tenant_scope(&operation) {
        return block("tenant_id_missing");
    }
    if requires_run_id(&operation) && run_id.is_none() {
        return block("run_id_missing");
    }
    if boolish(input, "workspace_access_denied", false) {
        return block("workspace_access_denied");
    }
    if boolish(input, "kill_switch_active", false) && mutates_runtime(&operation) {
        return block("run_api_kill_switch_active");
    }

    match operation.as_str() {
        "list_runs" => list_runs_decision(input, workspace_id, tenant_id, user_role),
        "get_run" => get_run_decision(input, workspace_id, tenant_id, run_id, user_role),
        "start_turn" | "start_run" => start_decision(
            input,
            &operation,
            workspace_id,
            tenant_id,
            run_id,
            user_role,
        ),
        "stream_chat" => stream_chat_decision(input, workspace_id, tenant_id, run_id, user_role),
        "cancel_run" => mutate_existing_run(
            "run_api_cancel_allowed",
            "cancel_run",
            "cancel_run",
            input,
            workspace_id,
            tenant_id,
            run_id,
            user_role,
            &run_status,
        ),
        "pause_run" => mutate_existing_run(
            "run_api_pause_allowed",
            "pause_run",
            "pause_run",
            input,
            workspace_id,
            tenant_id,
            run_id,
            user_role,
            &run_status,
        ),
        "retry_run" => retry_decision(
            input,
            workspace_id,
            tenant_id,
            run_id,
            user_role,
            &run_status,
        ),
        "resume_run" => resume_decision(
            input,
            workspace_id,
            tenant_id,
            run_id,
            user_role,
            &run_status,
        ),
        "approve_run" => approve_decision(input, workspace_id, tenant_id, run_id, user_role),
        "webhook_trigger" => webhook_decision(input, workspace_id, tenant_id, user_role),
        _ => block("run_api_operation_unknown"),
    }
}

fn list_runs_decision(
    input: &Value,
    workspace_id: Option<String>,
    tenant_id: Option<String>,
    user_role: String,
) -> Value {
    if !can_read(&user_role) {
        return block("run_api_actor_cannot_read");
    }
    if !boolish(input, "history_window_allowed", true) {
        return block("run_history_window_exceeded");
    }
    allow(
        "run_api_list_allowed",
        "list_runs",
        "list_runs",
        workspace_id,
        tenant_id,
        None,
        &user_role,
        true,
        "standard",
    )
}

fn get_run_decision(
    input: &Value,
    workspace_id: Option<String>,
    tenant_id: Option<String>,
    run_id: Option<String>,
    user_role: String,
) -> Value {
    if !can_read(&user_role) {
        return block("run_api_actor_cannot_read");
    }
    if boolish(input, "owner_access_denied", false) {
        return block("run_owner_access_denied");
    }
    if boolish(input, "sensitive_payload_requested", false) && !can_view_sensitive(&user_role) {
        return require_approval(
            "run_sensitive_payload_requires_approval",
            "get_run",
            "request_owner_approval",
            workspace_id,
            tenant_id,
            run_id,
            &user_role,
        );
    }
    allow(
        "run_api_get_allowed",
        "get_run",
        "get_run",
        workspace_id,
        tenant_id,
        run_id,
        &user_role,
        true,
        "standard",
    )
}

fn start_decision(
    input: &Value,
    operation: &str,
    workspace_id: Option<String>,
    tenant_id: Option<String>,
    run_id: Option<String>,
    user_role: String,
) -> Value {
    if !can_write(&user_role) {
        return block("run_api_actor_cannot_start");
    }
    if !has_user_input(input) {
        return block("run_api_input_missing");
    }
    if boolish(input, "entitlement_blocked", false) {
        return block("run_api_entitlement_blocked");
    }
    if boolish(input, "budget_exhausted", false) {
        return block("run_api_budget_exhausted");
    }
    if boolish(input, "local_execution_requested", false)
        && !boolish(input, "local_execution_approved", false)
    {
        return require_approval(
            "local_execution_requires_confirmation",
            operation,
            "request_local_execution_confirmation",
            workspace_id,
            tenant_id,
            run_id,
            &user_role,
        );
    }
    if boolish(input, "browser_automation_requested", false)
        && !boolish(input, "browser_execution_approved", false)
    {
        return require_approval(
            "browser_execution_requires_confirmation",
            operation,
            "request_browser_execution_confirmation",
            workspace_id,
            tenant_id,
            run_id,
            &user_role,
        );
    }
    allow(
        "run_api_start_allowed",
        operation,
        if operation == "start_turn" {
            "start_turn"
        } else {
            "start_run"
        },
        workspace_id,
        tenant_id,
        run_id,
        &user_role,
        false,
        "security",
    )
}

fn stream_chat_decision(
    input: &Value,
    workspace_id: Option<String>,
    tenant_id: Option<String>,
    run_id: Option<String>,
    user_role: String,
) -> Value {
    if !can_write(&user_role) {
        return block("run_api_actor_cannot_stream");
    }
    if !has_user_input(input) {
        return block("run_api_input_missing");
    }
    if boolish(input, "replay_requested", false) && boolish(input, "replay_session_missing", false)
    {
        return block("run_api_replay_session_missing");
    }
    allow(
        "run_api_stream_chat_allowed",
        "stream_chat",
        "start_chat_stream",
        workspace_id,
        tenant_id,
        run_id,
        &user_role,
        false,
        "security",
    )
}

fn mutate_existing_run(
    reason: &str,
    operation: &str,
    next_action: &str,
    input: &Value,
    workspace_id: Option<String>,
    tenant_id: Option<String>,
    run_id: Option<String>,
    user_role: String,
    run_status: &str,
) -> Value {
    if !can_write(&user_role) {
        return block("run_api_actor_cannot_mutate");
    }
    if boolish(input, "owner_access_denied", false) {
        return block("run_owner_access_denied");
    }
    if RUN_TERMINAL_STATUSES.contains(&run_status) && operation == "cancel_run" {
        return block("run_api_terminal_run_cannot_cancel");
    }
    allow(
        reason,
        operation,
        next_action,
        workspace_id,
        tenant_id,
        run_id,
        &user_role,
        false,
        "security",
    )
}

fn retry_decision(
    input: &Value,
    workspace_id: Option<String>,
    tenant_id: Option<String>,
    run_id: Option<String>,
    user_role: String,
    run_status: &str,
) -> Value {
    if !RUN_TERMINAL_STATUSES.contains(&run_status) && run_status != "blocked" {
        return block("run_api_retry_requires_terminal_or_blocked_run");
    }
    if boolish(input, "retry_budget_exhausted", false) {
        return block("run_api_retry_budget_exhausted");
    }
    mutate_existing_run(
        "run_api_retry_allowed",
        "retry_run",
        "retry_run",
        input,
        workspace_id,
        tenant_id,
        run_id,
        user_role,
        run_status,
    )
}

fn resume_decision(
    input: &Value,
    workspace_id: Option<String>,
    tenant_id: Option<String>,
    run_id: Option<String>,
    user_role: String,
    run_status: &str,
) -> Value {
    if !matches!(
        run_status,
        "pending_approval" | "interrupted" | "paused" | "waiting" | "waiting_for_input"
    ) {
        return block("run_api_resume_state_invalid");
    }
    mutate_existing_run(
        "run_api_resume_allowed",
        "resume_run",
        "resume_run",
        input,
        workspace_id,
        tenant_id,
        run_id,
        user_role,
        run_status,
    )
}

fn approve_decision(
    input: &Value,
    workspace_id: Option<String>,
    tenant_id: Option<String>,
    run_id: Option<String>,
    user_role: String,
) -> Value {
    if !can_approve(&user_role) {
        return block("run_api_actor_cannot_approve");
    }
    if !boolish(input, "approval_payload_present", false) {
        return block("run_api_approval_payload_missing");
    }
    allow(
        "run_api_approval_allowed",
        "approve_run",
        "resolve_run_approval",
        workspace_id,
        tenant_id,
        run_id,
        &user_role,
        false,
        "security",
    )
}

fn webhook_decision(
    input: &Value,
    workspace_id: Option<String>,
    tenant_id: Option<String>,
    user_role: String,
) -> Value {
    if !boolish(input, "webhook_signature_valid", false) {
        return block("webhook_signature_invalid");
    }
    if boolish(input, "webhook_disabled", false) {
        return block("webhook_disabled");
    }
    allow(
        "run_api_webhook_trigger_allowed",
        "webhook_trigger",
        "trigger_webhook_run",
        workspace_id,
        tenant_id,
        None,
        &user_role,
        false,
        "security",
    )
}

fn allow(
    reason: &str,
    operation: &str,
    next_action: &str,
    workspace_id: Option<String>,
    tenant_id: Option<String>,
    run_id: Option<String>,
    user_role: &str,
    cacheable: bool,
    audit_visibility: &str,
) -> Value {
    response(
        "allow",
        reason,
        operation,
        next_action,
        workspace_id,
        tenant_id,
        run_id,
        user_role,
        false,
        cacheable,
        audit_visibility,
    )
}

fn require_approval(
    reason: &str,
    operation: &str,
    next_action: &str,
    workspace_id: Option<String>,
    tenant_id: Option<String>,
    run_id: Option<String>,
    user_role: &str,
) -> Value {
    response(
        "require_approval",
        reason,
        operation,
        next_action,
        workspace_id,
        tenant_id,
        run_id,
        user_role,
        true,
        false,
        "security",
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
    next_action: &str,
    workspace_id: Option<String>,
    tenant_id: Option<String>,
    run_id: Option<String>,
    user_role: &str,
    approval_required: bool,
    cacheable: bool,
    audit_visibility: &str,
) -> Value {
    json!({
        "ok": decision != "block",
        "decision": decision,
        "reason": reason,
        "operation": operation,
        "next_action": next_action,
        "workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "run_id": run_id,
        "user_role": user_role,
        "approval_required": approval_required || decision == "require_approval",
        "cacheable": cacheable,
        "audit_visibility": audit_visibility,
    })
}

fn normalize_operation(value: &str) -> String {
    match normalize_token(value).as_str() {
        "list_runs" | "runs_list" | "get_runs" | "runs" => "list_runs".to_string(),
        "get_run" | "run_detail" | "read_run" => "get_run".to_string(),
        "start_turn" | "agent_turn" | "turn" | "thread_turn" => "start_turn".to_string(),
        "start_run" | "create_run" | "run_start" | "legacy_run_start" => "start_run".to_string(),
        "stream_chat" | "chat_stream" | "direct_chat_stream" => "stream_chat".to_string(),
        "cancel_run" | "cancel" => "cancel_run".to_string(),
        "retry_run" | "retry" => "retry_run".to_string(),
        "resume_run" | "resume" => "resume_run".to_string(),
        "approve_run" | "approval" | "resolve_approval" => "approve_run".to_string(),
        "webhook_trigger" | "trigger_webhook" => "webhook_trigger".to_string(),
        _ => String::new(),
    }
}

fn normalize_user_role(value: &str) -> String {
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

fn boolish(input: &Value, key: &str, default_value: bool) -> bool {
    if input.get(key).is_none() {
        return default_value;
    }
    match input.get(key) {
        Some(Value::Bool(value)) => *value,
        Some(Value::String(value)) => matches!(
            normalize_token(value).as_str(),
            "1" | "true" | "yes" | "on" | "enabled" | "active" | "allowed" | "approved"
        ),
        Some(Value::Number(value)) => value.as_u64().unwrap_or(0) > 0,
        _ => false,
    }
}

fn has_user_input(input: &Value) -> bool {
    string_field(input, "message")
        .or_else(|| string_field(input, "prompt"))
        .or_else(|| string_field(input, "input"))
        .is_some()
        || input.get("body").is_some()
}

fn requires_tenant_scope(operation: &str) -> bool {
    !matches!(operation, "webhook_trigger")
}

fn requires_run_id(operation: &str) -> bool {
    matches!(
        operation,
        "get_run" | "cancel_run" | "pause_run" | "retry_run" | "resume_run" | "approve_run"
    )
}

fn mutates_runtime(operation: &str) -> bool {
    !matches!(operation, "list_runs" | "get_run")
}

fn can_read(user_role: &str) -> bool {
    matches!(user_role, "system" | "owner" | "member" | "viewer")
}

fn can_write(user_role: &str) -> bool {
    matches!(user_role, "system" | "owner" | "member")
}

fn can_approve(user_role: &str) -> bool {
    matches!(user_role, "system" | "owner")
}

fn can_view_sensitive(user_role: &str) -> bool {
    matches!(user_role, "system" | "owner")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blocks_missing_operation() {
        let response = run_api_decision_command(&json!({"workspace_id": "w1"}));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "run_api_operation_missing");
    }

    #[test]
    fn list_runs_allows_viewer_inside_history_window() {
        let response = run_api_decision_command(&json!({
            "operation": "list_runs",
            "workspace_id": "w1",
            "tenant_id": "t1",
            "user_role": "viewer"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["cacheable"], true);
    }

    #[test]
    fn start_run_requires_input() {
        let response = run_api_decision_command(&json!({
            "operation": "start_run",
            "workspace_id": "w1",
            "tenant_id": "t1",
            "user_role": "member"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "run_api_input_missing");
    }

    #[test]
    fn local_execution_start_requires_confirmation() {
        let response = run_api_decision_command(&json!({
            "operation": "start_run",
            "workspace_id": "w1",
            "tenant_id": "t1",
            "user_role": "member",
            "prompt": "do work",
            "local_execution_requested": true
        }));
        assert_eq!(response["decision"], "require_approval");
        assert_eq!(
            response["next_action"],
            "request_local_execution_confirmation"
        );
    }

    #[test]
    fn terminal_run_cannot_cancel() {
        let response = run_api_decision_command(&json!({
            "operation": "cancel_run",
            "workspace_id": "w1",
            "tenant_id": "t1",
            "run_id": "run1",
            "user_role": "member",
            "run_status": "completed"
        }));
        assert_eq!(response["decision"], "block");
    }

    #[test]
    fn resume_allows_restored_waiting_for_input_run() {
        let response = run_api_decision_command(&json!({
            "operation": "resume_run",
            "workspace_id": "w1",
            "tenant_id": "t1",
            "run_id": "run1",
            "user_role": "system",
            "run_status": "waiting_for_input"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_action"], "resume_run");
    }

    #[test]
    fn webhook_requires_valid_signature() {
        let response = run_api_decision_command(&json!({
            "operation": "webhook_trigger",
            "workspace_id": "w1"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "webhook_signature_invalid");
    }
}

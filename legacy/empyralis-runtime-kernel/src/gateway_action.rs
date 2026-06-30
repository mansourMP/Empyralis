use serde_json::{json, Value};

pub fn gateway_action_decision_command(input: &Value) -> Value {
    let operation = normalize_operation(
        string_field(input, "operation")
            .or_else(|| string_field(input, "action"))
            .or_else(|| string_field(input, "endpoint"))
            .as_deref()
            .unwrap_or(""),
    );
    if operation.is_empty() {
        return block("gateway_action_operation_missing");
    }

    let workspace_id = string_field(input, "workspace_id");
    let tenant_id = string_field(input, "tenant_id");
    let gateway_id = string_field(input, "gateway_id");
    let run_id = string_field(input, "run_id");
    let request_id = string_field(input, "request_id");
    let capability_id = string_field(input, "capability_id");
    let actor_role = normalize_actor_role(
        string_field(input, "actor_role")
            .or_else(|| string_field(input, "user_role"))
            .or_else(|| string_field(input, "role"))
            .as_deref()
            .unwrap_or("viewer"),
    );

    if workspace_id.is_none() && requires_workspace(&operation) {
        return block("workspace_id_missing");
    }
    if gateway_id.is_none() && requires_gateway(&operation) {
        return block("gateway_id_missing");
    }
    if run_id.is_none() && requires_run(&operation) {
        return block("run_id_missing");
    }
    if boolish(input, "workspace_access_denied", false) {
        return block("workspace_access_denied");
    }
    if boolish(input, "kill_switch_active", false) && mutates_gateway(&operation) {
        return block("gateway_action_kill_switch_active");
    }
    if boolish(input, "quota_exceeded", false) {
        return block("gateway_action_quota_exceeded");
    }
    if boolish(input, "capability_disabled", false) {
        return block("gateway_action_capability_disabled");
    }

    match operation.as_str() {
        "health_check" => allow(
            "gateway_action_health_check_allowed",
            &operation,
            "return_health",
            workspace_id,
            tenant_id,
            gateway_id,
            run_id,
            request_id,
            capability_id,
            &actor_role,
            true,
            "standard",
        ),
        "websocket_connect" => websocket_connect_decision(
            input,
            workspace_id,
            tenant_id,
            gateway_id,
            run_id,
            request_id,
            capability_id,
            actor_role,
        ),
        "tool_execute" => tool_execute_decision(
            input,
            workspace_id,
            tenant_id,
            gateway_id,
            run_id,
            request_id,
            capability_id,
            actor_role,
        ),
        "browser_session_start"
        | "browser_action"
        | "browser_session_stop"
        | "browser_session_resume"
        | "browser_session_takeover" => browser_decision(
            input,
            &operation,
            workspace_id,
            tenant_id,
            gateway_id,
            run_id,
            request_id,
            capability_id,
            actor_role,
        ),
        "approval_resolve" => approval_resolve_decision(
            input,
            workspace_id,
            tenant_id,
            gateway_id,
            run_id,
            request_id,
            capability_id,
            actor_role,
        ),
        "acp_turn" => acp_turn_decision(
            input,
            workspace_id,
            tenant_id,
            gateway_id,
            run_id,
            request_id,
            capability_id,
            actor_role,
        ),
        "diagnostics_export" => diagnostics_decision(
            workspace_id,
            tenant_id,
            gateway_id,
            run_id,
            request_id,
            capability_id,
            actor_role,
        ),
        _ => block("gateway_action_operation_unknown"),
    }
}

fn websocket_connect_decision(
    input: &Value,
    workspace_id: Option<String>,
    tenant_id: Option<String>,
    gateway_id: Option<String>,
    run_id: Option<String>,
    request_id: Option<String>,
    capability_id: Option<String>,
    actor_role: String,
) -> Value {
    if !boolish(input, "session_token_valid", false) {
        return block("gateway_ws_session_token_invalid");
    }
    if !boolish(input, "registration_active", true) {
        return block("gateway_registration_inactive");
    }
    allow(
        "gateway_ws_connect_allowed",
        "websocket_connect",
        "accept_websocket",
        workspace_id,
        tenant_id,
        gateway_id,
        run_id,
        request_id,
        capability_id,
        &actor_role,
        false,
        "security",
    )
}

fn tool_execute_decision(
    input: &Value,
    workspace_id: Option<String>,
    tenant_id: Option<String>,
    gateway_id: Option<String>,
    run_id: Option<String>,
    request_id: Option<String>,
    capability_id: Option<String>,
    actor_role: String,
) -> Value {
    if capability_id.is_none() {
        return block("capability_id_missing");
    }
    match risk_decision(input).as_str() {
        "block" => return block("gateway_tool_risk_blocked"),
        "require_approval" => {
            if !approval_satisfied(input) {
                return require_approval(
                    "gateway_tool_requires_approval",
                    "tool_execute",
                    "request_gateway_tool_approval",
                    workspace_id,
                    tenant_id,
                    gateway_id,
                    run_id,
                    request_id,
                    capability_id,
                    &actor_role,
                );
            }
        }
        _ => {}
    }
    if !boolish(input, "gateway_connected", true) {
        return block("gateway_not_connected");
    }
    allow(
        "gateway_tool_execute_allowed",
        "tool_execute",
        "dispatch_tool_invoke",
        workspace_id,
        tenant_id,
        gateway_id,
        run_id,
        request_id,
        capability_id,
        &actor_role,
        false,
        "security",
    )
}

fn browser_decision(
    input: &Value,
    operation: &str,
    workspace_id: Option<String>,
    tenant_id: Option<String>,
    gateway_id: Option<String>,
    run_id: Option<String>,
    request_id: Option<String>,
    capability_id: Option<String>,
    actor_role: String,
) -> Value {
    if operation != "browser_session_start" && string_field(input, "browser_session_id").is_none() {
        return block("browser_session_id_missing");
    }
    match risk_decision(input).as_str() {
        "block" => return block("gateway_browser_risk_blocked"),
        "require_approval" => {
            if !approval_satisfied(input) {
                return require_approval(
                    "gateway_browser_requires_approval",
                    operation,
                    "request_gateway_browser_approval",
                    workspace_id,
                    tenant_id,
                    gateway_id,
                    run_id,
                    request_id,
                    capability_id,
                    &actor_role,
                );
            }
        }
        _ => {}
    }
    if boolish(input, "reviewed_approval_required", false) && !approval_satisfied(input) {
        return require_approval(
            "gateway_browser_reviewed_approval_required",
            operation,
            "request_gateway_browser_approval",
            workspace_id,
            tenant_id,
            gateway_id,
            run_id,
            request_id,
            capability_id,
            &actor_role,
        );
    }
    if !boolish(input, "gateway_connected", true) {
        if boolish(input, "cloud_fallback_available", false) {
            return allow(
                "gateway_browser_cloud_fallback_allowed",
                operation,
                "prepare_cloud_browser_fallback",
                workspace_id,
                tenant_id,
                gateway_id,
                run_id,
                request_id,
                capability_id,
                &actor_role,
                false,
                "security",
            );
        }
        return block("gateway_not_connected");
    }
    allow(
        "gateway_browser_action_allowed",
        operation,
        if operation == "browser_session_start" {
            "start_browser_session"
        } else if operation == "browser_session_stop" {
            "stop_browser_session"
        } else if operation == "browser_session_resume" {
            "resume_browser_session"
        } else if operation == "browser_session_takeover" {
            "takeover_browser_session"
        } else {
            "dispatch_browser_action"
        },
        workspace_id,
        tenant_id,
        gateway_id,
        run_id,
        request_id,
        capability_id,
        &actor_role,
        false,
        "security",
    )
}

fn approval_resolve_decision(
    input: &Value,
    workspace_id: Option<String>,
    tenant_id: Option<String>,
    gateway_id: Option<String>,
    run_id: Option<String>,
    request_id: Option<String>,
    capability_id: Option<String>,
    actor_role: String,
) -> Value {
    if !can_approve(&actor_role) {
        return block("gateway_approval_actor_cannot_resolve");
    }
    if string_field(input, "approval_id").is_none() {
        return block("approval_id_missing");
    }
    allow(
        "gateway_approval_resolve_allowed",
        "approval_resolve",
        "resolve_gateway_approval",
        workspace_id,
        tenant_id,
        gateway_id,
        run_id,
        request_id,
        capability_id,
        &actor_role,
        false,
        "security",
    )
}

fn acp_turn_decision(
    input: &Value,
    workspace_id: Option<String>,
    tenant_id: Option<String>,
    gateway_id: Option<String>,
    run_id: Option<String>,
    request_id: Option<String>,
    capability_id: Option<String>,
    actor_role: String,
) -> Value {
    if !boolish(input, "api_key_valid", false) {
        return block("gateway_acp_api_key_invalid");
    }
    if !has_user_input(input) && !boolish(input, "health_probe", false) {
        return block("gateway_acp_turn_payload_missing");
    }
    allow(
        "gateway_acp_turn_allowed",
        "acp_turn",
        "route_acp_turn",
        workspace_id,
        tenant_id,
        gateway_id,
        run_id,
        request_id,
        capability_id,
        &actor_role,
        false,
        "security",
    )
}

fn diagnostics_decision(
    workspace_id: Option<String>,
    tenant_id: Option<String>,
    gateway_id: Option<String>,
    run_id: Option<String>,
    request_id: Option<String>,
    capability_id: Option<String>,
    actor_role: String,
) -> Value {
    if !can_read(&actor_role) {
        return block("gateway_diagnostics_actor_cannot_read");
    }
    allow(
        "gateway_diagnostics_export_allowed",
        "diagnostics_export",
        "export_diagnostics_bundle",
        workspace_id,
        tenant_id,
        gateway_id,
        run_id,
        request_id,
        capability_id,
        &actor_role,
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
    gateway_id: Option<String>,
    run_id: Option<String>,
    request_id: Option<String>,
    capability_id: Option<String>,
    actor_role: &str,
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
        gateway_id,
        run_id,
        request_id,
        capability_id,
        actor_role,
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
    gateway_id: Option<String>,
    run_id: Option<String>,
    request_id: Option<String>,
    capability_id: Option<String>,
    actor_role: &str,
) -> Value {
    response(
        "require_approval",
        reason,
        operation,
        next_action,
        workspace_id,
        tenant_id,
        gateway_id,
        run_id,
        request_id,
        capability_id,
        actor_role,
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
    gateway_id: Option<String>,
    run_id: Option<String>,
    request_id: Option<String>,
    capability_id: Option<String>,
    actor_role: &str,
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
        "gateway_id": gateway_id,
        "run_id": run_id,
        "request_id": request_id,
        "capability_id": capability_id,
        "actor_role": actor_role,
        "approval_required": approval_required || decision == "require_approval",
        "cacheable": cacheable,
        "audit_visibility": audit_visibility,
    })
}

fn normalize_operation(value: &str) -> String {
    match normalize_token(value).as_str() {
        "health" | "health_check" => "health_check".to_string(),
        "websocket_connect" | "ws_connect" | "gateway_ws" => "websocket_connect".to_string(),
        "tool_execute" | "tool_invoke" | "execute_tool" => "tool_execute".to_string(),
        "browser_session_start" | "start_browser_session" | "browser_start" => {
            "browser_session_start".to_string()
        }
        "browser_action" | "browser_execute" | "browser_step" => "browser_action".to_string(),
        "browser_session_stop" | "stop_browser_session" | "browser_stop" => {
            "browser_session_stop".to_string()
        }
        "browser_session_resume" | "resume_browser_session" | "browser_resume" => {
            "browser_session_resume".to_string()
        }
        "browser_session_takeover" | "takeover_browser_session" | "browser_takeover" => {
            "browser_session_takeover".to_string()
        }
        "approval_resolve" | "resolve_approval" | "approval_action" => {
            "approval_resolve".to_string()
        }
        "acp_turn" | "agent_client_protocol_turn" => "acp_turn".to_string(),
        "diagnostics_export" | "export_diagnostics" => "diagnostics_export".to_string(),
        _ => String::new(),
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
            "1" | "true" | "yes" | "on" | "enabled" | "active" | "valid" | "approved"
        ),
        Some(Value::Number(value)) => value.as_u64().unwrap_or(0) > 0,
        _ => false,
    }
}

fn risk_decision(input: &Value) -> String {
    normalize_token(
        string_field(input, "risk_decision")
            .or_else(|| string_field(input, "risk"))
            .as_deref()
            .unwrap_or("allow"),
    )
}

fn approval_satisfied(input: &Value) -> bool {
    boolish(input, "approval_memory_matched", false)
        || boolish(input, "approval_granted", false)
        || boolish(input, "empyralis_approved", false)
        || boolish(input, "explicit_full_access", false)
}

fn has_user_input(input: &Value) -> bool {
    string_field(input, "message")
        .or_else(|| string_field(input, "prompt"))
        .or_else(|| string_field(input, "input"))
        .is_some()
        || input.get("body").is_some()
}

fn requires_workspace(operation: &str) -> bool {
    !matches!(operation, "health_check")
}

fn requires_gateway(operation: &str) -> bool {
    matches!(
        operation,
        "websocket_connect"
            | "tool_execute"
            | "browser_session_start"
            | "browser_action"
            | "browser_session_stop"
            | "browser_session_resume"
            | "browser_session_takeover"
            | "approval_resolve"
    )
}

fn requires_run(operation: &str) -> bool {
    matches!(
        operation,
        "tool_execute"
            | "browser_session_start"
            | "browser_action"
            | "browser_session_stop"
            | "browser_session_resume"
            | "browser_session_takeover"
    )
}

fn mutates_gateway(operation: &str) -> bool {
    !matches!(operation, "health_check" | "diagnostics_export")
}

fn can_read(actor_role: &str) -> bool {
    matches!(actor_role, "system" | "owner" | "member" | "viewer")
}

fn can_approve(actor_role: &str) -> bool {
    matches!(actor_role, "system" | "owner")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blocks_missing_operation() {
        let response = gateway_action_decision_command(&json!({"workspace_id": "w1"}));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "gateway_action_operation_missing");
    }

    #[test]
    fn websocket_requires_session_token() {
        let response = gateway_action_decision_command(&json!({
            "operation": "websocket_connect",
            "workspace_id": "w1",
            "gateway_id": "gw1"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "gateway_ws_session_token_invalid");
    }

    #[test]
    fn tool_execute_requires_approval_for_risk() {
        let response = gateway_action_decision_command(&json!({
            "operation": "tool_execute",
            "workspace_id": "w1",
            "gateway_id": "gw1",
            "run_id": "run1",
            "capability_id": "shell.exec",
            "risk_decision": "require_approval"
        }));
        assert_eq!(response["decision"], "require_approval");
    }

    #[test]
    fn browser_start_can_prepare_cloud_fallback() {
        let response = gateway_action_decision_command(&json!({
            "operation": "browser_session_start",
            "workspace_id": "w1",
            "gateway_id": "gw1",
            "run_id": "run1",
            "gateway_connected": false,
            "cloud_fallback_available": true
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_action"], "prepare_cloud_browser_fallback");
    }

    #[test]
    fn browser_resume_allows_resume_action() {
        let response = gateway_action_decision_command(&json!({
            "operation": "browser_session_resume",
            "workspace_id": "w1",
            "gateway_id": "gw1",
            "run_id": "run1",
            "browser_session_id": "sess1",
            "gateway_connected": true
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_action"], "resume_browser_session");
    }

    #[test]
    fn browser_takeover_allows_takeover_action() {
        let response = gateway_action_decision_command(&json!({
            "operation": "browser_session_takeover",
            "workspace_id": "w1",
            "gateway_id": "gw1",
            "run_id": "run1",
            "browser_session_id": "sess1",
            "gateway_connected": true
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_action"], "takeover_browser_session");
    }

    #[test]
    fn approval_resolve_requires_owner() {
        let response = gateway_action_decision_command(&json!({
            "operation": "approval_resolve",
            "workspace_id": "w1",
            "gateway_id": "gw1",
            "approval_id": "approval1",
            "actor_role": "member"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "gateway_approval_actor_cannot_resolve");
    }

    #[test]
    fn acp_turn_requires_api_key() {
        let response = gateway_action_decision_command(&json!({
            "operation": "acp_turn",
            "workspace_id": "w1",
            "message": "hello"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "gateway_acp_api_key_invalid");
    }
}

use serde_json::{json, Value};

pub fn runtime_binding_decision_command(input: &Value) -> Value {
    let operation = normalize_operation(
        string_field(input, "operation")
            .or_else(|| string_field(input, "action"))
            .or_else(|| string_field(input, "intent"))
            .as_deref()
            .unwrap_or("ensure_runtime_session"),
    );
    if operation.is_empty() {
        return block("runtime_binding_operation_unknown");
    }

    let mode = normalize_mode(
        string_field(input, "studio_agent_mode")
            .or_else(|| string_field(input, "mode"))
            .as_deref()
            .unwrap_or(""),
    );
    let deployed_agent_id =
        string_field(input, "deployed_agent_id").or_else(|| string_field(input, "agent_id"));
    let tenant_id = string_field(input, "tenant_id");
    let workspace_id = string_field(input, "workspace_id");
    let session_id = string_field(input, "session_id");
    let thread_id = string_field(input, "thread_id");
    let existing_runtime_session_id = string_field(input, "existing_runtime_session_id");
    let existing_runtime_binding = normalize_binding(
        string_field(input, "existing_runtime_binding")
            .or_else(|| string_field(input, "runtime_session_binding"))
            .as_deref()
            .unwrap_or(""),
    );
    let desired_binding = binding_for_mode(&mode).to_string();

    if deployed_agent_id.is_none() {
        return block("deployed_agent_id_missing");
    }
    if tenant_id.is_none() {
        return block("tenant_id_missing");
    }
    if workspace_id.is_none() {
        return block("workspace_id_missing");
    }
    if session_id.is_none() {
        return block("session_id_missing");
    }
    if mode.is_empty() {
        return block("studio_agent_mode_unknown");
    }
    if mode == "text" {
        return allow(
            "runtime_binding_text_mode_noop",
            &operation,
            "skip_runtime_binding",
            &mode,
            desired_binding.as_str(),
            deployed_agent_id,
            tenant_id,
            workspace_id,
            session_id,
            thread_id,
            None,
            None,
            None,
            true,
            "standard",
        );
    }
    if operation == "ensure_cloud_runtime_session" && mode == "self_hosted" {
        return block("self_hosted_requires_self_hosted_binding");
    }
    if operation == "ensure_self_hosted_runtime_session" && mode != "self_hosted" {
        return allow(
            "runtime_binding_not_self_hosted_noop",
            &operation,
            "skip_self_hosted_binding",
            &mode,
            desired_binding.as_str(),
            deployed_agent_id,
            tenant_id,
            workspace_id,
            session_id,
            thread_id,
            None,
            None,
            None,
            true,
            "standard",
        );
    }
    if existing_runtime_session_id.is_some() && existing_runtime_binding == desired_binding {
        return allow(
            "runtime_binding_existing_session_reused",
            &operation,
            "reuse_runtime_session",
            &mode,
            desired_binding.as_str(),
            deployed_agent_id,
            tenant_id,
            workspace_id,
            session_id,
            thread_id,
            existing_runtime_session_id,
            None,
            None,
            true,
            "standard",
        );
    }

    match mode.as_str() {
        "cloud_computer" => cloud_binding_decision(
            operation,
            mode,
            desired_binding.as_str(),
            deployed_agent_id,
            tenant_id,
            workspace_id,
            session_id,
            thread_id,
        ),
        "my_computer" => my_computer_binding_decision(
            input,
            operation,
            mode,
            desired_binding.as_str(),
            deployed_agent_id,
            tenant_id,
            workspace_id,
            session_id,
            thread_id,
        ),
        "self_hosted" => self_hosted_binding_decision(
            input,
            operation,
            mode,
            desired_binding.as_str(),
            deployed_agent_id,
            tenant_id,
            workspace_id,
            session_id,
            thread_id,
        ),
        _ => block("studio_agent_mode_not_runtime_bindable"),
    }
}

fn cloud_binding_decision(
    operation: String,
    mode: String,
    desired_binding: &str,
    deployed_agent_id: Option<String>,
    tenant_id: Option<String>,
    workspace_id: Option<String>,
    session_id: Option<String>,
    thread_id: Option<String>,
) -> Value {
    allow(
        "runtime_binding_cloud_session_allowed",
        &operation,
        "create_cloud_runtime_session",
        &mode,
        desired_binding,
        deployed_agent_id,
        tenant_id,
        workspace_id,
        session_id,
        thread_id,
        None,
        None,
        None,
        false,
        "security",
    )
}

fn my_computer_binding_decision(
    input: &Value,
    operation: String,
    mode: String,
    desired_binding: &str,
    deployed_agent_id: Option<String>,
    tenant_id: Option<String>,
    workspace_id: Option<String>,
    session_id: Option<String>,
    thread_id: Option<String>,
) -> Value {
    if !boolish(input, "local_gateway_attachment_present", false) {
        return block("local_gateway_attachment_missing");
    }
    if !boolish(input, "local_gateway_online", false) {
        return block("local_gateway_offline");
    }
    if !boolish(input, "local_gateway_healthy", false) {
        return block("local_gateway_unhealthy");
    }
    if boolish(input, "local_gateway_revoked", false) {
        return block("local_gateway_revoked");
    }
    allow(
        "runtime_binding_my_computer_session_allowed",
        &operation,
        "create_local_gateway_runtime_session",
        &mode,
        desired_binding,
        deployed_agent_id,
        tenant_id,
        workspace_id,
        session_id,
        thread_id,
        string_field(input, "runtime_attachment_id"),
        string_field(input, "gateway_id"),
        string_field(input, "runtime_node_id"),
        false,
        "security",
    )
}

fn self_hosted_binding_decision(
    input: &Value,
    operation: String,
    mode: String,
    desired_binding: &str,
    deployed_agent_id: Option<String>,
    tenant_id: Option<String>,
    workspace_id: Option<String>,
    session_id: Option<String>,
    thread_id: Option<String>,
) -> Value {
    let binding_workspace_id = string_field(input, "binding_workspace_id");
    if !boolish(input, "self_hosted_binding_present", false) {
        return block("self_hosted_binding_missing");
    }
    if string_field(input, "runtime_node_id").is_none() {
        return block("runtime_node_id_missing");
    }
    if string_field(input, "runtime_profile_id").is_none() {
        return block("runtime_profile_id_missing");
    }
    if string_field(input, "runtime_attachment_id").is_none() {
        return block("runtime_attachment_id_missing");
    }
    if binding_workspace_id.is_none() {
        return block("binding_workspace_id_missing");
    }
    if binding_workspace_id != workspace_id {
        return block("self_hosted_binding_workspace_mismatch");
    }
    if !boolish(input, "self_hosted_node_registered", false) {
        return block("self_hosted_node_not_registered");
    }
    if !boolish(input, "self_hosted_node_gate_passed", false) {
        return block("self_hosted_node_gate_failed");
    }
    if boolish(input, "agent_allowed_on_node", true) == false {
        return block("self_hosted_agent_not_allowed_on_node");
    }
    let active_sessions = positive_i64(input, "active_sessions", 0);
    let max_concurrent_sessions = positive_i64(input, "max_concurrent_sessions", 0);
    if max_concurrent_sessions > 0 && active_sessions >= max_concurrent_sessions {
        return block("self_hosted_node_concurrency_exceeded");
    }
    if normalize_token(
        string_field(input, "filesystem_scope")
            .as_deref()
            .unwrap_or("none"),
    )
    .as_str()
        == "workspace_scoped"
        && !boolish(input, "workspace_scoped_filesystem_allowed", false)
    {
        return block("workspace_scoped_filesystem_not_allowed");
    }
    if !boolish(input, "domain_allowlist_present", false) {
        return block("self_hosted_domain_allowlist_missing");
    }
    if !boolish(input, "session_recording_enabled", true) {
        return block("self_hosted_session_recording_required");
    }
    allow(
        "runtime_binding_self_hosted_session_allowed",
        &operation,
        "create_self_hosted_runtime_session",
        &mode,
        desired_binding,
        deployed_agent_id,
        tenant_id,
        workspace_id,
        session_id,
        thread_id,
        string_field(input, "runtime_attachment_id"),
        None,
        string_field(input, "runtime_node_id"),
        false,
        "security",
    )
}

fn allow(
    reason: &str,
    operation: &str,
    next_action: &str,
    mode: &str,
    runtime_session_binding: &str,
    deployed_agent_id: Option<String>,
    tenant_id: Option<String>,
    workspace_id: Option<String>,
    session_id: Option<String>,
    thread_id: Option<String>,
    runtime_session_id: Option<String>,
    runtime_attachment_id: Option<String>,
    runtime_node_id: Option<String>,
    cacheable: bool,
    audit_visibility: &str,
) -> Value {
    json!({
        "ok": true,
        "decision": "allow",
        "reason": reason,
        "operation": operation,
        "next_action": next_action,
        "studio_agent_mode": mode,
        "runtime_session_binding": runtime_session_binding,
        "deployed_agent_id": deployed_agent_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "session_id": session_id,
        "thread_id": thread_id,
        "runtime_session_id": runtime_session_id,
        "runtime_attachment_id": runtime_attachment_id,
        "runtime_node_id": runtime_node_id,
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
        "" | "ensure_runtime_session" | "runtime_session" => "ensure_runtime_session".to_string(),
        "ensure_cloud_runtime_session" | "cloud_runtime_session" => {
            "ensure_cloud_runtime_session".to_string()
        }
        "ensure_self_hosted_runtime_session" | "self_hosted_runtime_session" => {
            "ensure_self_hosted_runtime_session".to_string()
        }
        _ => String::new(),
    }
}

fn normalize_mode(value: &str) -> String {
    match normalize_token(value).as_str() {
        "text" | "chat" | "no_computer" => "text".to_string(),
        "cloud_computer" | "sage_cloud_computer" | "cloud" => "cloud_computer".to_string(),
        "my_computer" | "local_companion" | "local_gateway" => "my_computer".to_string(),
        "self_hosted" | "self_hosted_agent" | "self_host_runtime" => "self_hosted".to_string(),
        _ => String::new(),
    }
}

fn normalize_binding(value: &str) -> String {
    match normalize_token(value).as_str() {
        "cloud_computer_agent" | "cloud" => "cloud_computer_agent".to_string(),
        "my_computer_agent" | "local_gateway" | "local_companion" => {
            "my_computer_agent".to_string()
        }
        "self_hosted_agent" | "self_hosted" => "self_hosted_agent".to_string(),
        _ => String::new(),
    }
}

fn binding_for_mode(mode: &str) -> &str {
    match mode {
        "cloud_computer" => "cloud_computer_agent",
        "my_computer" => "my_computer_agent",
        "self_hosted" => "self_hosted_agent",
        _ => "",
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
            "1" | "true" | "yes" | "on" | "enabled" | "active" | "present" | "passed"
        ),
        Some(Value::Number(value)) => value.as_i64().unwrap_or(0) > 0,
        _ => false,
    }
}

fn positive_i64(input: &Value, key: &str, default_value: i64) -> i64 {
    if input.get(key).is_none() {
        return default_value;
    }
    match input.get(key) {
        Some(Value::Number(value)) => value.as_i64().unwrap_or(default_value).max(0),
        Some(Value::String(value)) => value.trim().parse::<i64>().unwrap_or(default_value).max(0),
        _ => default_value,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn text_mode_skips_binding() {
        let response = runtime_binding_decision_command(&json!({
            "operation": "ensure_runtime_session",
            "deployed_agent_id": "agent1",
            "tenant_id": "t1",
            "workspace_id": "w1",
            "session_id": "s1",
            "studio_agent_mode": "text"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_action"], "skip_runtime_binding");
    }

    #[test]
    fn cloud_operation_blocks_self_hosted_mode() {
        let response = runtime_binding_decision_command(&json!({
            "operation": "ensure_cloud_runtime_session",
            "deployed_agent_id": "agent1",
            "tenant_id": "t1",
            "workspace_id": "w1",
            "session_id": "s1",
            "studio_agent_mode": "self_hosted"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(
            response["reason"],
            "self_hosted_requires_self_hosted_binding"
        );
    }

    #[test]
    fn my_computer_requires_healthy_gateway() {
        let response = runtime_binding_decision_command(&json!({
            "operation": "ensure_runtime_session",
            "deployed_agent_id": "agent1",
            "tenant_id": "t1",
            "workspace_id": "w1",
            "session_id": "s1",
            "studio_agent_mode": "my_computer",
            "local_gateway_attachment_present": true,
            "local_gateway_online": true,
            "local_gateway_healthy": false
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "local_gateway_unhealthy");
    }

    #[test]
    fn self_hosted_requires_binding_contract() {
        let response = runtime_binding_decision_command(&json!({
            "operation": "ensure_runtime_session",
            "deployed_agent_id": "agent1",
            "tenant_id": "t1",
            "workspace_id": "w1",
            "session_id": "s1",
            "studio_agent_mode": "self_hosted"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "self_hosted_binding_missing");
    }

    #[test]
    fn self_hosted_blocks_concurrency_exceeded() {
        let response = runtime_binding_decision_command(&json!({
            "operation": "ensure_runtime_session",
            "deployed_agent_id": "agent1",
            "tenant_id": "t1",
            "workspace_id": "w1",
            "session_id": "s1",
            "studio_agent_mode": "self_hosted",
            "self_hosted_binding_present": true,
            "binding_workspace_id": "w1",
            "runtime_node_id": "node1",
            "runtime_profile_id": "profile1",
            "runtime_attachment_id": "attachment1",
            "self_hosted_node_registered": true,
            "self_hosted_node_gate_passed": true,
            "domain_allowlist_present": true,
            "active_sessions": 2,
            "max_concurrent_sessions": 2
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "self_hosted_node_concurrency_exceeded");
    }

    #[test]
    fn reuses_existing_matching_binding() {
        let response = runtime_binding_decision_command(&json!({
            "operation": "ensure_runtime_session",
            "deployed_agent_id": "agent1",
            "tenant_id": "t1",
            "workspace_id": "w1",
            "session_id": "s1",
            "studio_agent_mode": "cloud_computer",
            "existing_runtime_session_id": "runtime1",
            "existing_runtime_binding": "cloud_computer_agent"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_action"], "reuse_runtime_session");
    }
}

#[cfg(test)]
mod runtime_binding_kernel_boundary_tests {
    use super::runtime_binding_decision_command;

    #[test]
    fn cloud_runtime_binding_requires_workspace_identity() {
        let decision = runtime_binding_decision_command(&serde_json::json!({
            "operation": "ensure_cloud_runtime_session",
            "deployed_agent_id": "dagent_1",
            "tenant_id": "tenant-1",
            "session_id": "sess-1",
            "studio_agent_mode": "cloud_computer_agent"
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("block")
        );
        assert_eq!(
            decision.get("reason").and_then(|value| value.as_str()),
            Some("workspace_id_missing")
        );
    }

    #[test]
    fn self_hosted_cannot_use_cloud_runtime_binding() {
        let decision = runtime_binding_decision_command(&serde_json::json!({
            "operation": "ensure_cloud_runtime_session",
            "deployed_agent_id": "dagent_1",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "session_id": "sess-1",
            "studio_agent_mode": "self_hosted_agent"
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("block")
        );
        assert_eq!(
            decision.get("reason").and_then(|value| value.as_str()),
            Some("self_hosted_requires_self_hosted_binding")
        );
    }
}

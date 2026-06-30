use serde_json::{json, Value};

pub fn runtime_action_decision_command(input: &Value) -> Value {
    let operation = normalize_operation(
        string_field(input, "operation")
            .or_else(|| string_field(input, "action"))
            .or_else(|| string_field(input, "intent"))
            .as_deref()
            .unwrap_or("execute_runtime_action"),
    );
    if operation.is_empty() {
        return block("runtime_action_operation_unknown");
    }

    let runtime_binding = normalize_binding(
        string_field(input, "runtime_session_binding")
            .or_else(|| string_field(input, "binding"))
            .as_deref()
            .unwrap_or(""),
    );
    let mode = normalize_mode(
        string_field(input, "studio_agent_mode")
            .or_else(|| string_field(input, "mode"))
            .as_deref()
            .unwrap_or(""),
    );
    let connector_id = normalize_token(
        string_field(input, "connector_id")
            .or_else(|| string_field(input, "connector"))
            .as_deref()
            .unwrap_or(""),
    );
    let action_id = normalize_token(
        string_field(input, "action_id")
            .or_else(|| string_field(input, "tool_action"))
            .as_deref()
            .unwrap_or(""),
    );
    let deployed_agent_id =
        string_field(input, "deployed_agent_id").or_else(|| string_field(input, "agent_id"));
    let tenant_id = string_field(input, "tenant_id");
    let workspace_id = string_field(input, "workspace_id");
    let runtime_session_id =
        string_field(input, "runtime_session_id").or_else(|| string_field(input, "session_id"));
    let run_id = string_field(input, "run_id");
    let thread_id = string_field(input, "thread_id");

    if connector_id.is_empty() || action_id.is_empty() {
        return block("runtime_action_connector_action_missing");
    }
    if deployed_agent_id.is_none() {
        return block("deployed_agent_id_missing");
    }
    if tenant_id.is_none() {
        return block("tenant_id_missing");
    }
    if workspace_id.is_none() {
        return block("workspace_id_missing");
    }
    if runtime_session_id.is_none() {
        return block("runtime_session_id_missing");
    }
    if boolish(input, "raw_policy_override_present", false)
        || positive_i64(input, "forbidden_override_key_count", 0) > 0
    {
        return block("runtime_action_policy_override_blocked");
    }
    if boolish(input, "kill_switch_active", false)
        || boolish(input, "workspace_emergency_stop", false)
        || boolish(input, "agent_paused", false)
        || boolish(input, "agent_suspended", false)
        || boolish(input, "agent_archived", false)
    {
        return block("runtime_action_kill_state_blocked");
    }

    match runtime_binding.as_str() {
        "cloud_computer_agent" => cloud_action_decision(
            input,
            mode,
            connector_id,
            action_id,
            deployed_agent_id,
            tenant_id,
            workspace_id,
            runtime_session_id,
            run_id,
            thread_id,
        ),
        "self_hosted_agent" => self_hosted_action_decision(
            input,
            mode,
            connector_id,
            action_id,
            deployed_agent_id,
            tenant_id,
            workspace_id,
            runtime_session_id,
            run_id,
            thread_id,
        ),
        _ => block("runtime_action_binding_not_supported"),
    }
}

fn cloud_action_decision(
    input: &Value,
    mode: String,
    connector_id: String,
    action_id: String,
    deployed_agent_id: Option<String>,
    tenant_id: Option<String>,
    workspace_id: Option<String>,
    runtime_session_id: Option<String>,
    run_id: Option<String>,
    thread_id: Option<String>,
) -> Value {
    if mode != "cloud_computer" {
        return allow(
            "runtime_action_cloud_mode_mismatch_noop",
            "execute_runtime_action",
            "skip_runtime_action",
            "cloud_computer_agent",
            &mode,
            "",
            deployed_agent_id,
            tenant_id,
            workspace_id,
            runtime_session_id,
            run_id,
            thread_id,
            false,
            true,
            "standard",
        );
    }
    let Some((runtime_action, proof_required)) =
        runtime_action_for(&connector_id, &action_id, input)
    else {
        return block("runtime_action_not_supported");
    };
    allow(
        "runtime_action_cloud_allowed",
        "execute_runtime_action",
        "execute_cloud_runtime_action",
        "cloud_computer_agent",
        &mode,
        runtime_action,
        deployed_agent_id,
        tenant_id,
        workspace_id,
        runtime_session_id,
        run_id,
        thread_id,
        proof_required,
        false,
        "security",
    )
}

fn self_hosted_action_decision(
    input: &Value,
    mode: String,
    connector_id: String,
    action_id: String,
    deployed_agent_id: Option<String>,
    tenant_id: Option<String>,
    workspace_id: Option<String>,
    runtime_session_id: Option<String>,
    run_id: Option<String>,
    thread_id: Option<String>,
) -> Value {
    if mode != "self_hosted" {
        return allow(
            "runtime_action_self_hosted_mode_mismatch_noop",
            "execute_runtime_action",
            "skip_runtime_action",
            "self_hosted_agent",
            &mode,
            "",
            deployed_agent_id,
            tenant_id,
            workspace_id,
            runtime_session_id,
            run_id,
            thread_id,
            false,
            true,
            "standard",
        );
    }
    if !boolish(input, "self_hosted_node_gate_passed", false) {
        return block("self_hosted_node_gate_failed");
    }
    if boolish(input, "self_hosted_node_concurrency_exceeded", false) {
        return block("self_hosted_node_concurrency_exceeded");
    }
    let Some((runtime_action, proof_required)) =
        runtime_action_for(&connector_id, &action_id, input)
    else {
        return block("runtime_action_not_supported");
    };
    allow(
        "runtime_action_self_hosted_allowed",
        "execute_runtime_action",
        "execute_self_hosted_runtime_action",
        "self_hosted_agent",
        &mode,
        runtime_action,
        deployed_agent_id,
        tenant_id,
        workspace_id,
        runtime_session_id,
        run_id,
        thread_id,
        proof_required,
        false,
        "security",
    )
}

fn runtime_action_for(
    connector_id: &str,
    action_id: &str,
    input: &Value,
) -> Option<(&'static str, bool)> {
    match (connector_id, action_id) {
        ("browser", "navigate")
        | ("browser", "open")
        | ("browser", "open_url")
        | ("browser", "new_tab") => {
            if string_field(input, "url").is_none() {
                None
            } else {
                Some(("open_url", false))
            }
        }
        ("browser", "download_file") => {
            if string_field(input, "url").is_none() {
                None
            } else {
                Some(("download_artifact", false))
            }
        }
        ("browser", "screenshot") | ("screenshot", "capture") => {
            if string_field(input, "selector").is_some() {
                None
            } else {
                Some(("screenshot", true))
            }
        }
        ("computer", "click") => {
            if input.get("x").is_none() || input.get("y").is_none() {
                None
            } else {
                Some(("click", true))
            }
        }
        ("computer", "type") => {
            if string_field(input, "text")
                .or_else(|| string_field(input, "input"))
                .is_none()
            {
                None
            } else {
                Some(("type", true))
            }
        }
        ("shell", "exec") | ("shell", "execute") => {
            if string_field(input, "command").is_none() {
                None
            } else {
                Some(("run_command", false))
            }
        }
        _ => None,
    }
}

fn allow(
    reason: &str,
    operation: &str,
    next_action: &str,
    runtime_session_binding: &str,
    mode: &str,
    runtime_action: &str,
    deployed_agent_id: Option<String>,
    tenant_id: Option<String>,
    workspace_id: Option<String>,
    runtime_session_id: Option<String>,
    run_id: Option<String>,
    thread_id: Option<String>,
    proof_required: bool,
    cacheable: bool,
    audit_visibility: &str,
) -> Value {
    json!({
        "ok": true,
        "decision": "allow",
        "reason": reason,
        "operation": operation,
        "next_action": next_action,
        "runtime_session_binding": runtime_session_binding,
        "studio_agent_mode": mode,
        "runtime_action": runtime_action,
        "deployed_agent_id": deployed_agent_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "runtime_session_id": runtime_session_id,
        "run_id": run_id,
        "thread_id": thread_id,
        "computer_proof_required": proof_required,
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
        "" | "execute_runtime_action" | "runtime_action" | "tool_call" => {
            "execute_runtime_action".to_string()
        }
        _ => String::new(),
    }
}

fn normalize_mode(value: &str) -> String {
    match normalize_token(value).as_str() {
        "cloud_computer" | "sage_cloud_computer" | "cloud" => "cloud_computer".to_string(),
        "self_hosted" | "self_hosted_agent" | "self_host_runtime" => "self_hosted".to_string(),
        "my_computer" | "local_companion" | "local_gateway" => "my_computer".to_string(),
        "text" | "chat" => "text".to_string(),
        _ => String::new(),
    }
}

fn normalize_binding(value: &str) -> String {
    match normalize_token(value).as_str() {
        "cloud_computer_agent" | "cloud" => "cloud_computer_agent".to_string(),
        "self_hosted_agent" | "self_hosted" => "self_hosted_agent".to_string(),
        "my_computer_agent" | "local_gateway" | "local_companion" => {
            "my_computer_agent".to_string()
        }
        _ => String::new(),
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
    fn blocks_missing_connector_action() {
        let response = runtime_action_decision_command(&json!({
            "runtime_session_binding": "cloud_computer_agent",
            "studio_agent_mode": "cloud_computer",
            "deployed_agent_id": "agent1",
            "tenant_id": "t1",
            "workspace_id": "w1",
            "runtime_session_id": "runtime1"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(
            response["reason"],
            "runtime_action_connector_action_missing"
        );
    }

    #[test]
    fn blocks_raw_policy_override() {
        let response = runtime_action_decision_command(&json!({
            "runtime_session_binding": "cloud_computer_agent",
            "studio_agent_mode": "cloud_computer",
            "connector_id": "browser",
            "action_id": "navigate",
            "url": "https://example.com",
            "deployed_agent_id": "agent1",
            "tenant_id": "t1",
            "workspace_id": "w1",
            "runtime_session_id": "runtime1",
            "raw_policy_override_present": true
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "runtime_action_policy_override_blocked");
    }

    #[test]
    fn cloud_browser_navigate_allowed() {
        let response = runtime_action_decision_command(&json!({
            "runtime_session_binding": "cloud_computer_agent",
            "studio_agent_mode": "cloud_computer",
            "connector_id": "browser",
            "action_id": "navigate",
            "url": "https://example.com",
            "deployed_agent_id": "agent1",
            "tenant_id": "t1",
            "workspace_id": "w1",
            "runtime_session_id": "runtime1"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["runtime_action"], "open_url");
    }

    #[test]
    fn screenshot_selector_is_not_supported() {
        let response = runtime_action_decision_command(&json!({
            "runtime_session_binding": "cloud_computer_agent",
            "studio_agent_mode": "cloud_computer",
            "connector_id": "browser",
            "action_id": "screenshot",
            "selector": "#app",
            "deployed_agent_id": "agent1",
            "tenant_id": "t1",
            "workspace_id": "w1",
            "runtime_session_id": "runtime1"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "runtime_action_not_supported");
    }

    #[test]
    fn self_hosted_requires_node_gate() {
        let response = runtime_action_decision_command(&json!({
            "runtime_session_binding": "self_hosted_agent",
            "studio_agent_mode": "self_hosted",
            "connector_id": "shell",
            "action_id": "exec",
            "command": "pwd",
            "deployed_agent_id": "agent1",
            "tenant_id": "t1",
            "workspace_id": "w1",
            "runtime_session_id": "runtime1"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "self_hosted_node_gate_failed");
    }

    #[test]
    fn self_hosted_shell_exec_allowed_after_gate() {
        let response = runtime_action_decision_command(&json!({
            "runtime_session_binding": "self_hosted_agent",
            "studio_agent_mode": "self_hosted",
            "connector_id": "shell",
            "action_id": "exec",
            "command": "pwd",
            "deployed_agent_id": "agent1",
            "tenant_id": "t1",
            "workspace_id": "w1",
            "runtime_session_id": "runtime1",
            "self_hosted_node_gate_passed": true
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["runtime_action"], "run_command");
    }
}

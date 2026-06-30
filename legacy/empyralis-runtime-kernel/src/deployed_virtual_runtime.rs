use serde_json::{json, Value};

const KNOWN_PROVIDER_IDS: &[&str] = &[
    "browserbase",
    "e2b",
    "daytona",
    "aws_workspaces",
    "azure_virtual_desktop",
    "docker_kubernetes",
    "digitalocean_ssh",
];

const DISABLED_RECORDING_POLICIES: &[&str] = &["disabled", "off", "none"];
const FORBIDDEN_POLICY_OVERRIDE_KEYS: &[&str] = &[
    "computer_automation",
    "cost_quota",
    "runtime_quota",
    "network_policy",
    "enterprise_controls",
    "runtime_kill_state",
    "session_recording",
    "policy_metadata",
    "runtime_provider_id",
    "runtime_choice",
    "runtime_node_id",
    "runtime_profile_id",
    "runtime_attachment_id",
    "self_hosted_runtime_binding",
];

pub fn deployed_virtual_runtime_decision_command(payload: &Value) -> Value {
    let operation = normalize_operation(&text_any(payload, &["operation", "action"]));
    if operation.is_empty() {
        return block("virtual_runtime_operation_missing", "unknown");
    }
    if !known_operation(&operation) {
        return block("virtual_runtime_operation_unknown", &operation);
    }

    let tenant_id = text_any(payload, &["tenant_id", "owner_tenant_id"]);
    let workspace_id = text_any(payload, &["workspace_id", "owner_workspace_id"]);
    let deployed_agent_id = text_any(payload, &["deployed_agent_id", "agent_id"]);
    if tenant_id.is_empty() {
        return block("tenant_id_missing", &operation);
    }
    if workspace_id.is_empty() {
        return block("workspace_id_missing", &operation);
    }
    if deployed_agent_id.is_empty() {
        return block("deployed_agent_id_missing", &operation);
    }

    if bool_field(payload, "kill_switch_active")
        || bool_field(payload, "workspace_emergency_stop_active")
    {
        return block("virtual_runtime_kill_state_active", &operation);
    }
    let deployment_state = normalize_token(&text_any(payload, &["deployment_state"]));
    if matches!(
        deployment_state.as_str(),
        "archived" | "deleted" | "suspended"
    ) {
        return block("virtual_runtime_deployment_not_active", &operation);
    }

    match operation.as_str() {
        "build_policy_payload" => {
            build_policy_payload_decision(payload, &tenant_id, &workspace_id, &deployed_agent_id)
        }
        "select_provider" => {
            select_provider_decision(payload, &tenant_id, &workspace_id, &deployed_agent_id)
        }
        "session_policy" => {
            session_policy_decision(payload, &tenant_id, &workspace_id, &deployed_agent_id)
        }
        "cloud_tool_action" => {
            cloud_tool_action_decision(payload, &tenant_id, &workspace_id, &deployed_agent_id)
        }
        "usage_metering" => {
            usage_metering_decision(payload, &tenant_id, &workspace_id, &deployed_agent_id)
        }
        _ => block("virtual_runtime_operation_unknown", &operation),
    }
}

fn build_policy_payload_decision(
    payload: &Value,
    tenant_id: &str,
    workspace_id: &str,
    deployed_agent_id: &str,
) -> Value {
    let mode = normalize_mode(&text_any(payload, &["studio_agent_mode", "mode"]));
    if mode == "self_hosted_agent" {
        return block(
            "self_hosted_agent_requires_dedicated_payload",
            "build_policy_payload",
        );
    }
    if !bool_field(payload, "privacy_contract_valid") {
        return block("privacy_contract_snapshot_required", "build_policy_payload");
    }
    if requires_computer_runtime(&mode) && !bool_field(payload, "computer_safety_contract_valid") {
        return block(
            "computer_safety_contract_snapshot_required",
            "build_policy_payload",
        );
    }
    if requires_computer_runtime(&mode) {
        if domain_count(payload) == 0 {
            return block("domain_allowlist_required", "build_policy_payload");
        }
        if numeric_any(
            payload,
            &[
                "daily_budget_usd_cents",
                "monthly_budget_usd_cents",
                "cost_cap_usd_cents",
            ],
        )
        .unwrap_or(0)
            == 0
        {
            return block("runtime_budget_required", "build_policy_payload");
        }
        let recording_policy = normalize_token(&text_any(
            payload,
            &["session_recording_policy", "recording_policy"],
        ));
        if DISABLED_RECORDING_POLICIES.contains(&recording_policy.as_str()) {
            return block("session_recording_required", "build_policy_payload");
        }
    }
    allow(
        "virtual_runtime_policy_payload_allowed",
        "build_policy_payload",
        "build_deployed_agent_virtual_runtime_payload",
        tenant_id,
        workspace_id,
        deployed_agent_id,
        false,
        "security",
    )
    .with_extra("runtime_choice", json!(runtime_choice(payload, &mode)))
    .with_extra("studio_agent_mode", json!(mode))
    .with_extra(
        "session_recording_enabled",
        json!(recording_enabled(payload)),
    )
}

fn select_provider_decision(
    payload: &Value,
    tenant_id: &str,
    workspace_id: &str,
    deployed_agent_id: &str,
) -> Value {
    let provider_id = normalize_token(&text_any(
        payload,
        &[
            "runtime_provider_id",
            "runtime_provider",
            "virtual_runtime_provider",
            "cloud_computer_provider",
            "internal_provider",
        ],
    ));
    if provider_id.is_empty() {
        return allow(
            "virtual_runtime_provider_default_allowed",
            "select_provider",
            "use_default_virtual_runtime_provider",
            tenant_id,
            workspace_id,
            deployed_agent_id,
            true,
            "standard",
        );
    }
    if !KNOWN_PROVIDER_IDS.contains(&provider_id.as_str()) {
        return block("virtual_runtime_provider_unknown", "select_provider");
    }
    allow(
        "virtual_runtime_provider_allowed",
        "select_provider",
        "select_virtual_runtime_provider",
        tenant_id,
        workspace_id,
        deployed_agent_id,
        true,
        "standard",
    )
    .with_extra("runtime_provider_id", json!(provider_id))
}

fn session_policy_decision(
    payload: &Value,
    tenant_id: &str,
    workspace_id: &str,
    deployed_agent_id: &str,
) -> Value {
    let max_concurrent_sessions =
        numeric_any(payload, &["max_concurrent_sessions", "concurrency_limit"]).unwrap_or(1);
    if max_concurrent_sessions == 0 {
        return block("runtime_concurrency_limit_invalid", "session_policy");
    }
    let idle_timeout_seconds = numeric_any(payload, &["idle_timeout_seconds"]).unwrap_or(600);
    let max_runtime_seconds = numeric_any(
        payload,
        &["max_runtime_seconds", "max_session_runtime_seconds"],
    )
    .unwrap_or(3600);
    if idle_timeout_seconds == 0
        || max_runtime_seconds == 0
        || idle_timeout_seconds > max_runtime_seconds
    {
        return block("runtime_timeout_policy_invalid", "session_policy");
    }
    let active_sessions =
        numeric_any(payload, &["active_sessions", "active_session_count"]).unwrap_or(0);
    if active_sessions >= max_concurrent_sessions && !bool_field(payload, "reuse_existing_session")
    {
        return block("runtime_concurrency_limit_exceeded", "session_policy");
    }
    allow(
        "virtual_runtime_session_policy_allowed",
        "session_policy",
        "apply_virtual_runtime_session_policy",
        tenant_id,
        workspace_id,
        deployed_agent_id,
        false,
        "security",
    )
    .with_extra("max_concurrent_sessions", json!(max_concurrent_sessions))
    .with_extra("idle_timeout_seconds", json!(idle_timeout_seconds))
    .with_extra("max_runtime_seconds", json!(max_runtime_seconds))
}

fn cloud_tool_action_decision(
    payload: &Value,
    tenant_id: &str,
    workspace_id: &str,
    deployed_agent_id: &str,
) -> Value {
    if has_forbidden_override(payload.get("arguments").unwrap_or(&Value::Null)) {
        return block("runtime_policy_override_forbidden", "cloud_tool_action");
    }
    let connector = normalize_token(&text_any(payload, &["connector_id", "connector"]));
    let action = normalize_token(&text_any(
        payload,
        &["action_id", "tool_action", "action_name"],
    ));
    if connector.is_empty() || action.is_empty() {
        return block("runtime_tool_action_missing", "cloud_tool_action");
    }
    let mapped_action = match (connector.as_str(), action.as_str()) {
        ("browser", "navigate") | ("browser", "new_tab") => {
            if text_from_arguments(payload, "url").is_empty() {
                return block("browser_action_url_required", "cloud_tool_action");
            }
            "open_url"
        }
        ("browser", "download_file") => {
            if text_from_arguments(payload, "url").is_empty() {
                return block("browser_download_url_required", "cloud_tool_action");
            }
            "download_artifact"
        }
        ("browser", "screenshot") => {
            if !text_from_arguments(payload, "selector").is_empty() {
                return block(
                    "browser_screenshot_selector_unsupported",
                    "cloud_tool_action",
                );
            }
            "screenshot"
        }
        ("computer", "click") => {
            if !argument_exists(payload, "x") || !argument_exists(payload, "y") {
                return block("computer_click_coordinates_required", "cloud_tool_action");
            }
            "click"
        }
        ("computer", "type") => {
            if text_from_arguments(payload, "text").is_empty()
                && text_from_arguments(payload, "input").is_empty()
            {
                return block("computer_type_text_required", "cloud_tool_action");
            }
            "type"
        }
        ("computer", "screenshot") => "screenshot",
        ("shell", "run") | ("shell", "exec") => {
            if !bool_field(payload, "shell_runtime_enabled") {
                return block("shell_runtime_disabled", "cloud_tool_action");
            }
            if text_from_arguments(payload, "command").is_empty() {
                return block("shell_command_required", "cloud_tool_action");
            }
            "run_shell_command"
        }
        _ => return block("runtime_tool_action_unsupported", "cloud_tool_action"),
    };
    allow(
        "virtual_runtime_tool_action_allowed",
        "cloud_tool_action",
        mapped_action,
        tenant_id,
        workspace_id,
        deployed_agent_id,
        false,
        "security",
    )
    .with_extra("connector_id", json!(connector))
    .with_extra("action_id", json!(action))
}

fn usage_metering_decision(
    payload: &Value,
    tenant_id: &str,
    workspace_id: &str,
    deployed_agent_id: &str,
) -> Value {
    let runtime_target = normalize_token(&text_any(payload, &["runtime_target", "target"]));
    let billable_seconds =
        numeric_any(payload, &["billable_seconds", "duration_seconds"]).unwrap_or(0);
    let max_seconds = numeric_any(payload, &["max_runtime_seconds", "max_seconds"])
        .unwrap_or(billable_seconds.max(1));
    let capped_seconds = billable_seconds.min(max_seconds);
    let billable = if runtime_target == "sage_cloud_computer" {
        capped_seconds
    } else {
        0
    };
    allow(
        "virtual_runtime_usage_metering_allowed",
        "usage_metering",
        "record_bound_cloud_runtime_usage",
        tenant_id,
        workspace_id,
        deployed_agent_id,
        false,
        "standard",
    )
    .with_extra("billable_seconds", json!(billable))
    .with_extra("runtime_target", json!(runtime_target))
}

fn allow(
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
        "decision": "allow",
        "reason": reason,
        "operation": operation,
        "next_action": next_action,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "deployed_agent_id": deployed_agent_id,
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
        "payload" | "runtime_payload" | "build_runtime_payload" => {
            "build_policy_payload".to_string()
        }
        "provider" | "provider_select" => "select_provider".to_string(),
        "session" | "session_quota" => "session_policy".to_string(),
        "tool_action" | "runtime_tool_action" => "cloud_tool_action".to_string(),
        "usage" | "metering" => "usage_metering".to_string(),
        other => other.to_string(),
    }
}

fn known_operation(operation: &str) -> bool {
    matches!(
        operation,
        "build_policy_payload"
            | "select_provider"
            | "session_policy"
            | "cloud_tool_action"
            | "usage_metering"
    )
}

fn normalize_mode(raw: &str) -> String {
    match normalize_token(raw).as_str() {
        "cloud" | "cloud_computer" => "cloud_computer_agent".to_string(),
        "my_computer" | "local" | "local_companion" => "my_computer_agent".to_string(),
        "self_hosted" => "self_hosted_agent".to_string(),
        other => other.to_string(),
    }
}

fn requires_computer_runtime(mode: &str) -> bool {
    matches!(
        mode,
        "cloud_computer_agent" | "my_computer_agent" | "self_hosted_agent"
    )
}

fn runtime_choice(payload: &Value, mode: &str) -> String {
    let runtime_class = normalize_token(&text_any(
        payload,
        &["runtime_class", "computer_runtime_class"],
    ));
    match runtime_class.as_str() {
        "virtual_browser" => "virtual_browser".to_string(),
        "virtual_desktop" => "virtual_desktop".to_string(),
        "virtual_code_sandbox" => "virtual_code_sandbox".to_string(),
        "local_browser" | "local_desktop" => "local".to_string(),
        _ if mode == "cloud_computer_agent" => "virtual_browser".to_string(),
        _ if mode == "my_computer_agent" => "local".to_string(),
        _ => String::new(),
    }
}

fn recording_enabled(payload: &Value) -> bool {
    let policy = normalize_token(&text_any(
        payload,
        &["session_recording_policy", "recording_policy"],
    ));
    !DISABLED_RECORDING_POLICIES.contains(&policy.as_str())
}

fn domain_count(payload: &Value) -> u64 {
    numeric_any(payload, &["allowed_domain_count", "domain_allowlist_count"]).unwrap_or_else(|| {
        payload
            .get("allowed_domains")
            .and_then(Value::as_array)
            .map(|items| items.len() as u64)
            .unwrap_or(0)
    })
}

fn has_forbidden_override(value: &Value) -> bool {
    match value {
        Value::Object(map) => map.iter().any(|(key, child)| {
            FORBIDDEN_POLICY_OVERRIDE_KEYS.contains(&normalize_token(key).as_str())
                || has_forbidden_override(child)
        }),
        Value::Array(items) => items.iter().any(has_forbidden_override),
        _ => false,
    }
}

fn text_from_arguments(payload: &Value, key: &str) -> String {
    payload
        .get("arguments")
        .and_then(Value::as_object)
        .and_then(|arguments| arguments.get(key))
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string()
}

fn argument_exists(payload: &Value, key: &str) -> bool {
    payload
        .get("arguments")
        .and_then(Value::as_object)
        .map(|arguments| arguments.contains_key(key))
        .unwrap_or(false)
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

fn normalize_token(raw: &str) -> String {
    raw.trim().to_ascii_lowercase().replace('-', "_")
}

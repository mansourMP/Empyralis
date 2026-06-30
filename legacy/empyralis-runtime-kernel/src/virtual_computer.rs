use serde_json::{json, Value};

const KNOWN_PROVIDERS: &[&str] = &[
    "browserbase",
    "e2b",
    "daytona",
    "aws_workspaces",
    "azure_virtual_desktop",
    "docker_kubernetes",
    "digitalocean_ssh",
];
const VALID_ACTIONS: &[&str] = &[
    "screenshot",
    "click",
    "type",
    "scroll",
    "hotkey",
    "wait",
    "open_url",
    "download_artifact",
    "run_command",
    "kill_switch",
];
const VALID_ARTIFACT_TYPES: &[&str] = &[
    "screenshot",
    "pdf",
    "csv",
    "downloaded_file",
    "generated_document",
    "browser_trace",
    "terminal_log",
];

const MAX_TYPE_TEXT_LEN: usize = 4096;
const MAX_URL_LEN: usize = 2048;
const MAX_COMMAND_LEN: usize = 1024;
const MAX_KEY_TOKEN_LEN: usize = 32;
const MAX_WAIT_MS: u64 = 120_000;
const MAX_COORDINATE: i64 = 50_000;

pub fn virtual_computer_decision_command(payload: &Value) -> Value {
    let operation = normalize_operation(&text_any(payload, &["operation", "action"]));
    if operation.is_empty() {
        return block("virtual_computer_operation_missing", "unknown");
    }
    if !known_operation(&operation) {
        return block("virtual_computer_operation_unknown", &operation);
    }

    if bool_field(payload, "kill_switch_active") && mutates_runtime(&operation) {
        return block("virtual_computer_kill_switch_active", &operation);
    }

    match operation.as_str() {
        "provider_admission" => provider_admission(payload),
        "isolation_profile" => isolation_profile(payload),
        "identity_context" => identity_context(payload),
        "cost_quota" => cost_quota(payload),
        "action_payload" => action_payload(payload),
        "network_policy" => network_policy(payload),
        "artifact_export" => artifact_export(payload),
        "session_state" => session_state(payload),
        _ => block("virtual_computer_operation_unknown", &operation),
    }
}

fn provider_admission(payload: &Value) -> Value {
    let provider_id = normalize_token(&text_any(payload, &["provider_id", "runtime_provider_id"]));
    let runtime_choice =
        normalize_runtime_choice(&text_any(payload, &["runtime_choice", "runtime_target"]));
    if provider_id.is_empty() && runtime_choice != "local" {
        if bool_field(payload, "cloud_environment")
            && !bool_field(payload, "inmemory_runtime_allowed")
        {
            return block("virtual_provider_required_in_cloud", "provider_admission");
        }
        return allow(
            "virtual_provider_default_allowed",
            "provider_admission",
            "use_default_virtual_provider",
            "",
            &runtime_choice,
            true,
            "standard",
        );
    }
    if !provider_id.is_empty() && !KNOWN_PROVIDERS.contains(&provider_id.as_str()) {
        return block("virtual_provider_unknown", "provider_admission");
    }
    if bool_field(payload, "inmemory_runtime")
        && runtime_choice != "local"
        && bool_field(payload, "cloud_environment")
        && !bool_field(payload, "inmemory_runtime_allowed")
    {
        return block("inmemory_runtime_blocked_in_cloud", "provider_admission");
    }
    allow(
        "virtual_provider_admitted",
        "provider_admission",
        "admit_virtual_computer_provider",
        &provider_id,
        &runtime_choice,
        true,
        "standard",
    )
}

fn isolation_profile(payload: &Value) -> Value {
    let runtime_choice =
        normalize_runtime_choice(&text_any(payload, &["runtime_choice", "runtime_target"]));
    let min_filesystem = 1_u64 * 1024 * 1024;
    let filesystem_quota = numeric_any(payload, &["filesystem_quota_bytes"]).unwrap_or_else(|| {
        if runtime_choice == "local" {
            500 * 1024 * 1024
        } else {
            250 * 1024 * 1024
        }
    });
    if filesystem_quota < min_filesystem {
        return block("filesystem_quota_too_small", "isolation_profile");
    }
    let cpu_quota_seconds = numeric_any(payload, &["cpu_quota_seconds"]).unwrap_or(120);
    if cpu_quota_seconds < 10 {
        return block("cpu_quota_too_small", "isolation_profile");
    }
    let memory_quota_mb = numeric_any(payload, &["memory_quota_mb"]).unwrap_or(768);
    if memory_quota_mb < 128 {
        return block("memory_quota_too_small", "isolation_profile");
    }
    let runtime_ttl_seconds = numeric_any(payload, &["runtime_ttl_seconds"]).unwrap_or(1800);
    if runtime_ttl_seconds < 60 {
        return block("runtime_ttl_too_small", "isolation_profile");
    }
    allow(
        "virtual_isolation_profile_allowed",
        "isolation_profile",
        "build_isolation_profile",
        "",
        &runtime_choice,
        true,
        "security",
    )
    .with_extra("filesystem_quota_bytes", json!(filesystem_quota))
    .with_extra("cpu_quota_seconds", json!(cpu_quota_seconds))
    .with_extra("memory_quota_mb", json!(memory_quota_mb))
    .with_extra("runtime_ttl_seconds", json!(runtime_ttl_seconds))
}

fn identity_context(payload: &Value) -> Value {
    let runtime_choice =
        normalize_runtime_choice(&text_any(payload, &["runtime_choice", "runtime_target"]));
    let identity_class = normalize_token(&text_any(payload, &["identity_class"]));
    let login_state = normalize_token(&text_any(payload, &["login_state"]));
    if runtime_choice != "local" && bool_field(payload, "requests_local_identity_reuse") {
        return block(
            "local_identity_reuse_blocked_for_virtual_runtime",
            "identity_context",
        );
    }
    if runtime_choice != "local" && bool_field(payload, "allow_cookie_reuse") {
        return block("cookie_reuse_override_blocked", "identity_context");
    }
    if login_state == "credential_broker_grant"
        && text_any(payload, &["credential_grant_id"]).is_empty()
    {
        return block("credential_broker_grant_required", "identity_context");
    }
    allow(
        "virtual_identity_context_allowed",
        "identity_context",
        "build_identity_context",
        "",
        &runtime_choice,
        true,
        "security",
    )
    .with_extra(
        "identity_class",
        json!(if identity_class.is_empty() {
            if runtime_choice == "local" {
                "user_local_identity"
            } else {
                "agent_virtual_identity"
            }
        } else {
            identity_class.as_str()
        }),
    )
    .with_extra(
        "login_state",
        json!(if login_state.is_empty() {
            if runtime_choice == "local" {
                "user_interactive_login"
            } else {
                "unauthenticated"
            }
        } else {
            login_state.as_str()
        }),
    )
}

fn cost_quota(payload: &Value) -> Value {
    let provider_concurrency = numeric_any(payload, &["provider_concurrency_limit"])
        .unwrap_or(4)
        .max(1);
    let workspace_concurrency = numeric_any(
        payload,
        &["workspace_concurrency_limit", "max_concurrent_sessions"],
    )
    .unwrap_or(provider_concurrency)
    .max(1);
    let agent_concurrency = numeric_any(payload, &["agent_concurrency_limit"])
        .unwrap_or(provider_concurrency)
        .max(1);
    let active_provider = numeric_any(payload, &["active_provider_sessions"]).unwrap_or(0);
    let active_workspace = numeric_any(payload, &["active_workspace_sessions"]).unwrap_or(0);
    let active_agent = numeric_any(payload, &["active_agent_sessions"]).unwrap_or(0);
    if active_provider >= provider_concurrency {
        return block("provider_concurrency_exceeded", "cost_quota");
    }
    if active_workspace >= workspace_concurrency {
        return block("workspace_concurrency_exceeded", "cost_quota");
    }
    if active_agent >= agent_concurrency {
        return block("agent_concurrency_exceeded", "cost_quota");
    }
    let threshold_action = normalize_token(&text_any(payload, &["budget_threshold_action"]));
    if !threshold_action.is_empty() && !matches!(threshold_action.as_str(), "pause" | "kill") {
        return block("budget_threshold_action_invalid", "cost_quota");
    }
    allow(
        "virtual_cost_quota_allowed",
        "cost_quota",
        "build_cost_quota_profile",
        "",
        &normalize_runtime_choice(&text_any(payload, &["runtime_choice", "runtime_target"])),
        true,
        "standard",
    )
    .with_extra("provider_concurrency_limit", json!(provider_concurrency))
    .with_extra("workspace_concurrency_limit", json!(workspace_concurrency))
    .with_extra("agent_concurrency_limit", json!(agent_concurrency))
}

fn action_payload(payload: &Value) -> Value {
    let action = normalize_action(&text_any(
        payload,
        &["computer_action", "tool_action", "virtual_action"],
    ));
    if action.is_empty() {
        return block("virtual_action_missing", "action_payload");
    }
    if !VALID_ACTIONS.contains(&action.as_str()) {
        return block("virtual_action_unsupported", "action_payload");
    }
    if bool_field(payload, "session_terminated") {
        return block("virtual_session_terminated", "action_payload");
    }
    if bool_field(payload, "session_expired") {
        return block("virtual_session_expired", "action_payload");
    }
    match action.as_str() {
        "click" => {
            if coordinate(payload, "x").is_none() || coordinate(payload, "y").is_none() {
                return block("click_coordinates_required", "action_payload");
            }
        }
        "type" => {
            let text = text_arg(payload, "text");
            if text.is_empty() {
                return block("type_text_required", "action_payload");
            }
            if text.len() > MAX_TYPE_TEXT_LEN {
                return block("type_text_too_long", "action_payload");
            }
        }
        "scroll" => {
            if coordinate(payload, "delta_x").is_none() && coordinate(payload, "delta_y").is_none()
            {
                return block("scroll_delta_required", "action_payload");
            }
        }
        "hotkey" => {
            let key_count = numeric_any(payload, &["key_count", "hotkey_count"]).unwrap_or(0);
            if key_count == 0 || key_count > 6 {
                return block("hotkey_count_invalid", "action_payload");
            }
            if bool_field(payload, "hotkey_token_too_long") {
                return block("hotkey_token_too_long", "action_payload");
            }
        }
        "wait" => {
            let duration_ms = numeric_any(payload, &["duration_ms"])
                .unwrap_or_else(|| numeric_any(payload, &["seconds"]).unwrap_or(0) * 1000);
            if duration_ms > MAX_WAIT_MS {
                return block("wait_duration_invalid", "action_payload");
            }
        }
        "open_url" | "download_artifact" => {
            let url = text_arg(payload, "url");
            if url.is_empty() {
                return block("url_required", "action_payload");
            }
            if url.len() > MAX_URL_LEN {
                return block("url_too_long", "action_payload");
            }
        }
        "run_command" => {
            let command = text_arg(payload, "command");
            if command.trim().is_empty() {
                return block("command_required", "action_payload");
            }
            if command.len() > MAX_COMMAND_LEN {
                return block("command_too_long", "action_payload");
            }
            if !bool_field(payload, "approval_granted") {
                return require_approval(
                    "run_command_requires_approval",
                    "action_payload",
                    "request_run_command_approval",
                    &action,
                );
            }
        }
        "kill_switch" => {
            if !bool_field(payload, "kill_switch_enabled") {
                return block("kill_switch_not_enabled", "action_payload");
            }
        }
        _ => {}
    }
    if bool_field(payload, "requires_approval") && !bool_field(payload, "approval_granted") {
        return require_approval(
            "computer_action_requires_approval",
            "action_payload",
            "request_computer_action_approval",
            &action,
        );
    }
    allow(
        "virtual_action_payload_allowed",
        "action_payload",
        "validate_computer_use_action_payload",
        "",
        &action,
        false,
        "security",
    )
}

fn network_policy(payload: &Value) -> Value {
    let action = normalize_action(&text_any(
        payload,
        &["computer_action", "tool_action", "virtual_action"],
    ));
    let host = normalize_host(&text_any(payload, &["host", "target_host"]));
    if bool_field(payload, "metadata_endpoint") {
        return block("metadata_endpoint_blocked", "network_policy");
    }
    if bool_field(payload, "private_lan_host") && !bool_field(payload, "allow_private_lan") {
        return block("private_lan_blocked", "network_policy");
    }
    if bool_field(payload, "denied_domain_match") {
        return block("denied_domain_blocked", "network_policy");
    }
    if bool_field(payload, "allowlist_enabled") && !bool_field(payload, "allowlist_match") {
        return block("non_allowlisted_domain_blocked", "network_policy");
    }
    if bool_field(payload, "phishing_detected") {
        return block("phishing_page_blocked", "network_policy");
    }
    if action == "download_artifact"
        && bool_field(payload, "auto_download")
        && bool_field(payload, "block_auto_download_without_approval")
        && !bool_field(payload, "approval_granted")
    {
        return require_approval(
            "automatic_download_requires_approval",
            "network_policy",
            "request_download_approval",
            &action,
        );
    }
    allow(
        "virtual_network_policy_allowed",
        "network_policy",
        "assert_network_browser_security",
        &host,
        &action,
        true,
        "security",
    )
}

fn artifact_export(payload: &Value) -> Value {
    let artifact_type = normalize_token(&text_any(payload, &["artifact_type", "type"]));
    if artifact_type.is_empty() {
        return block("artifact_type_missing", "artifact_export");
    }
    if !VALID_ARTIFACT_TYPES.contains(&artifact_type.as_str()) {
        return block("artifact_type_unsupported", "artifact_export");
    }
    let export_target = normalize_token(&text_any(payload, &["export_target", "target"]));
    if export_target == "local_computer" && !bool_field(payload, "local_gateway_approved") {
        return require_approval(
            "local_artifact_export_requires_gateway_approval",
            "artifact_export",
            "request_local_artifact_export_approval",
            &artifact_type,
        );
    }
    allow(
        "virtual_artifact_export_allowed",
        "artifact_export",
        "collect_virtual_computer_artifact",
        "",
        &artifact_type,
        false,
        "security",
    )
    .with_extra(
        "export_target",
        json!(if export_target.is_empty() {
            "workspace"
        } else {
            export_target.as_str()
        }),
    )
}

fn session_state(payload: &Value) -> Value {
    let state = normalize_token(&text_any(
        payload,
        &["state", "runtime_state", "session_state"],
    ));
    if matches!(state.as_str(), "terminated" | "failed") {
        return block("virtual_session_terminal", "session_state");
    }
    if state == "expired" || bool_field(payload, "session_expired") {
        return block("virtual_session_expired", "session_state");
    }
    let action = normalize_operation(&text_any(payload, &["session_operation", "session_action"]));
    if action == "resume_session" && state != "paused" && state != "ready" {
        return block("virtual_session_not_resumable", "session_state");
    }
    allow(
        "virtual_session_state_allowed",
        "session_state",
        "assert_virtual_session_active",
        "",
        &state,
        true,
        "standard",
    )
}

fn allow(
    reason: &str,
    operation: &str,
    next_action: &str,
    provider_id: &str,
    runtime_choice: &str,
    cacheable: bool,
    audit_visibility: &str,
) -> Value {
    json!({
        "ok": true,
        "decision": "allow",
        "reason": reason,
        "operation": operation,
        "next_action": next_action,
        "provider_id": provider_id,
        "runtime_choice": runtime_choice,
        "approval_required": false,
        "cacheable": cacheable,
        "audit_visibility": audit_visibility,
    })
}

fn require_approval(reason: &str, operation: &str, next_action: &str, target: &str) -> Value {
    json!({
        "ok": true,
        "decision": "require_approval",
        "reason": reason,
        "operation": operation,
        "next_action": next_action,
        "target": target,
        "approval_required": true,
        "approval_scope": format!("virtual-computer:{operation}:{target}"),
        "cacheable": false,
        "audit_visibility": "security",
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
        "provider" | "runtime_provider" => "provider_admission".to_string(),
        "isolation" | "profile" => "isolation_profile".to_string(),
        "identity" => "identity_context".to_string(),
        "quota" | "cost" => "cost_quota".to_string(),
        "action" | "execute_action" => "action_payload".to_string(),
        "network" | "browser_security" => "network_policy".to_string(),
        "artifact" | "collect_artifact" => "artifact_export".to_string(),
        "session" | "assert_session_active" => "session_state".to_string(),
        "resume" => "resume_session".to_string(),
        other => other.to_string(),
    }
}

fn known_operation(operation: &str) -> bool {
    matches!(
        operation,
        "provider_admission"
            | "isolation_profile"
            | "identity_context"
            | "cost_quota"
            | "action_payload"
            | "network_policy"
            | "artifact_export"
            | "session_state"
    )
}

fn mutates_runtime(operation: &str) -> bool {
    matches!(
        operation,
        "provider_admission" | "action_payload" | "artifact_export" | "session_state"
    )
}

fn normalize_runtime_choice(raw: &str) -> String {
    match normalize_token(raw).as_str() {
        "local_companion" | "local_browser" | "local_desktop" => "local".to_string(),
        "browser" => "virtual_browser".to_string(),
        "desktop" | "cloud_computer" => "virtual_desktop".to_string(),
        "code_sandbox" | "sandbox" => "virtual_code_sandbox".to_string(),
        other => other.to_string(),
    }
}

fn normalize_action(raw: &str) -> String {
    match normalize_token(raw).as_str() {
        "navigate" => "open_url".to_string(),
        "download_file" => "download_artifact".to_string(),
        other => other.to_string(),
    }
}

fn coordinate(payload: &Value, key: &str) -> Option<i64> {
    let value = payload
        .get("action_args")
        .and_then(Value::as_object)
        .and_then(|args| args.get(key))
        .or_else(|| payload.get(key))?;
    let parsed = match value {
        Value::Number(number) => number.as_i64()?,
        Value::String(text) => text.trim().parse::<i64>().ok()?,
        _ => return None,
    };
    if (0..=MAX_COORDINATE).contains(&parsed) {
        Some(parsed)
    } else {
        None
    }
}

fn text_arg(payload: &Value, key: &str) -> String {
    payload
        .get("action_args")
        .and_then(Value::as_object)
        .and_then(|args| args.get(key))
        .or_else(|| payload.get(key))
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string()
}

fn normalize_host(raw: &str) -> String {
    raw.trim().to_ascii_lowercase()
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
    raw.trim()
        .to_ascii_lowercase()
        .replace('-', "_")
        .replace(' ', "_")
}

#[cfg(test)]
mod virtual_computer_extension_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn identity_context_blocks_cookie_reuse_for_cloud_runtime() {
        let decision = virtual_computer_decision_command(&json!({
            "operation": "identity_context",
            "runtime_choice": "virtual_browser",
            "allow_cookie_reuse": true
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "cookie_reuse_override_blocked");
    }

    #[test]
    fn identity_context_defaults_to_agent_virtual_identity_for_cloud_runtime() {
        let decision = virtual_computer_decision_command(&json!({
            "operation": "identity_context",
            "runtime_choice": "virtual_browser"
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["identity_class"], "agent_virtual_identity");
        assert_eq!(decision["login_state"], "unauthenticated");
    }
}

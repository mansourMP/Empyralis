use crate::protocol;
use serde_json::{json, Value};

const READ_CAPABILITIES: &[&str] = &["read_files"];

const WRITE_CAPABILITIES: &[&str] = &["write_files", "git_write", "external_write"];

const EXECUTION_CAPABILITIES: &[&str] = &[
    "shell_command",
    "browser_control",
    "mcp_server",
    "long_running_task",
    "process_control",
    "system_control",
];

const SECURITY_CAPABILITIES: &[&str] = &[
    "secret_access",
    "destructive_action",
    "system_control",
    "process_control",
];

const NETWORK_CAPABILITIES: &[&str] = &["network_access", "browser_control", "external_write"];

const ALL_CAPABILITIES: &[&str] = &[
    "read_files",
    "write_files",
    "shell_command",
    "network_access",
    "browser_control",
    "mcp_server",
    "long_running_task",
    "process_control",
    "git_write",
    "secret_access",
    "external_write",
    "destructive_action",
    "system_control",
];

pub fn capability_manifest_command(input: &Value) -> Value {
    let include_aliases = protocol::string_field(input, "include_aliases")
        .map(|value| matches!(value.as_str(), "1" | "true" | "yes" | "on"))
        .unwrap_or(true);

    let aliases = if include_aliases {
        json!({
            "read_files": ["read_file", "read_files", "file_read", "filesystem_read"],
            "write_files": ["write_file", "write_files", "file_write", "filesystem_write"],
            "shell_command": ["shell", "shell_command", "command", "terminal", "exec", "execute"],
            "network_access": ["network", "network_access", "http", "web_request"],
            "browser_control": ["browser", "browser_control", "web_browser"],
            "mcp_server": ["mcp", "mcp_server", "tool_server"],
            "long_running_task": ["long_running", "long_running_task", "background_task"],
            "process_control": ["process", "process_control", "kill_process"],
            "git_write": ["git", "git_write", "git_push", "git_commit"],
            "secret_access": ["secret", "secrets", "secret_access", "credential_access"],
            "external_write": ["external_write", "external_side_effect", "send_email", "calendar_write"],
            "destructive_action": ["destructive", "destructive_action", "delete", "delete_data"],
            "system_control": ["system", "system_control", "os_control"],
        })
    } else {
        json!({})
    };

    json!({
        "ok": true,
        "decision": "allow",
        "reason": "capability_manifest",
        "next_action": "return_capability_manifest",
        "version": "phase6-rust-kernel-v1",
        "capabilities": ALL_CAPABILITIES,
        "classes": {
            "read": READ_CAPABILITIES,
            "write": WRITE_CAPABILITIES,
            "execution": EXECUTION_CAPABILITIES,
            "security_sensitive": SECURITY_CAPABILITIES,
            "network": NETWORK_CAPABILITIES,
        },
        "risk_defaults": {
            "low": ["read_files"],
            "medium": ["network_access", "browser_control", "long_running_task"],
            "high": ["shell_command", "write_files", "git_write", "external_write", "mcp_server"],
            "critical": ["system_control", "process_control", "secret_access", "destructive_action"],
        },
        "aliases": aliases,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn manifest_contains_security_sensitive_capabilities() {
        let response = capability_manifest_command(&json!({}));
        assert_eq!(response["next_action"], "return_capability_manifest");
        let security = response["classes"]["security_sensitive"]
            .as_array()
            .unwrap();
        assert!(security
            .iter()
            .any(|value| value.as_str() == Some("secret_access")));
        assert!(security
            .iter()
            .any(|value| value.as_str() == Some("destructive_action")));
    }

    #[test]
    fn manifest_aliases_can_be_omitted() {
        let response = capability_manifest_command(&json!({"include_aliases": "false"}));
        assert_eq!(response["next_action"], "return_capability_manifest");
        assert_eq!(response["aliases"], json!({}));
    }
}

use crate::policy::normalize_token;
use crate::protocol;
use serde_json::{json, Value};

const DEFAULT_RUNTIME_SECONDS: u64 = 120;
const MAX_RUNTIME_SECONDS: u64 = 3600;
const DEFAULT_MEMORY: &str = "512m";
const DEFAULT_CPU_SHARES: u64 = 512;

const BLOCKED_COMMAND_MARKERS: &[&str] = &[
    "rm -rf /",
    "rm -rf /*",
    "mkfs",
    "diskutil erase",
    "format c:",
    "shutdown",
    "reboot",
    "launchctl unload",
    "chmod -r 777 /",
    "chown -r ",
    "dd if=",
    ":(){ :|:& };:",
];

pub fn execution_plan_command(input: &Value) -> Value {
    let command = command_text(input);
    if command.is_empty() {
        return json!({
            "ok": false,
            "decision": "block",
            "reason": "execution_command_missing",
            "next_action": "deny_tool_execution",
            "execution": null,
            "cacheable": false,
            "audit_visibility": "security",
        });
    }

    let normalized_command = command.to_ascii_lowercase();
    if let Some(marker) = BLOCKED_COMMAND_MARKERS
        .iter()
        .find(|marker| normalized_command.contains(**marker))
    {
        return json!({
            "ok": false,
            "decision": "block",
            "reason": "destructive_command_blocked",
            "next_action": "deny_tool_execution",
            "matched_marker": marker,
            "execution": null,
            "cacheable": false,
            "audit_visibility": "security",
        });
    }

    let sandbox_required = boolish(input, "sandbox_required", true);
    let sandbox_type = normalize_sandbox_type(
        protocol::string_field(input, "sandbox_type")
            .as_deref()
            .unwrap_or("docker"),
    );
    if sandbox_required && sandbox_type == "none" {
        return json!({
            "ok": false,
            "decision": "block",
            "reason": "sandbox_required",
            "next_action": "deny_tool_execution",
            "execution": null,
            "cacheable": false,
            "audit_visibility": "security",
        });
    }

    let requested_network = boolish(input, "network_requested", false)
        || boolish(input, "requires_network", false)
        || command_requests_network(&normalized_command);
    let network_policy = if requested_network {
        "approval_required"
    } else {
        "none"
    };
    let max_runtime_seconds = bounded_u64(
        input,
        "max_runtime_seconds",
        DEFAULT_RUNTIME_SECONDS,
        1,
        MAX_RUNTIME_SECONDS,
    );
    let memory =
        protocol::string_field(input, "memory").unwrap_or_else(|| DEFAULT_MEMORY.to_string());
    let cpu_shares = bounded_u64(input, "cpu_shares", DEFAULT_CPU_SHARES, 2, 262_144);
    let working_dir = protocol::string_field(input, "working_dir")
        .or_else(|| protocol::string_field(input, "cwd"))
        .unwrap_or_else(|| "/workspace".to_string());

    let decision = if requested_network {
        "require_approval"
    } else {
        "allow"
    };
    let reason = if requested_network {
        "network_execution_requires_approval"
    } else {
        "execution_plan_ready"
    };

    json!({
        "ok": true,
        "decision": decision,
        "reason": reason,
        "next_action": if decision == "require_approval" {
            "request_tool_execution_approval"
        } else {
            "allow_tool_execution"
        },
        "execution": {
            "command": command,
            "working_dir": working_dir,
            "sandbox_required": sandbox_required,
            "sandbox_type": sandbox_type,
            "network": network_policy,
            "max_runtime_seconds": max_runtime_seconds,
            "memory": memory,
            "cpu_shares": cpu_shares,
            "read_only_root": true,
            "cap_drop": "ALL",
            "no_new_privileges": true,
        },
        "approval_required": decision == "require_approval",
        "cacheable": false,
        "audit_visibility": if decision == "allow" { "standard" } else { "security" },
    })
}

fn command_text(input: &Value) -> String {
    if let Some(command) = protocol::string_field(input, "command")
        .or_else(|| protocol::string_field(input, "shell_command"))
    {
        return command;
    }
    input
        .get("argv")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(str::trim)
                .filter(|item| !item.is_empty())
                .collect::<Vec<_>>()
                .join(" ")
        })
        .unwrap_or_default()
}

fn boolish(input: &Value, key: &str, default_value: bool) -> bool {
    match input.get(key) {
        Some(Value::Bool(value)) => *value,
        Some(Value::String(value)) => matches!(
            normalize_token(value).as_str(),
            "1" | "true" | "yes" | "on" | "enabled" | "active" | "required"
        ),
        Some(Value::Number(value)) => value.as_u64().unwrap_or(0) > 0,
        _ => default_value,
    }
}

fn bounded_u64(input: &Value, key: &str, default_value: u64, minimum: u64, maximum: u64) -> u64 {
    let value = input
        .get(key)
        .and_then(Value::as_u64)
        .unwrap_or(default_value);
    value.clamp(minimum, maximum)
}

fn normalize_sandbox_type(value: &str) -> String {
    match normalize_token(value).as_str() {
        "docker" | "container" => "docker".to_string(),
        "sandbox_exec" | "sandboxexec" | "macos_sandbox" => "sandbox_exec".to_string(),
        "subprocess" | "process" | "host" => "subprocess".to_string(),
        "none" | "disabled" | "off" => "none".to_string(),
        _ => "docker".to_string(),
    }
}

fn command_requests_network(command: &str) -> bool {
    [
        "curl ",
        "wget ",
        "npm install",
        "pip install",
        "git clone",
        "gh ",
        "brew install",
    ]
    .iter()
    .any(|marker| command.contains(marker))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_command_blocks() {
        let response = execution_plan_command(&json!({}));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "execution_command_missing");
        assert_eq!(response["next_action"], "deny_tool_execution");
    }

    #[test]
    fn destructive_command_blocks() {
        let response = execution_plan_command(&json!({"command": "rm -rf /"}));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "destructive_command_blocked");
        assert_eq!(response["next_action"], "deny_tool_execution");
    }

    #[test]
    fn sandbox_required_blocks_none_sandbox() {
        let response = execution_plan_command(&json!({
            "command": "python3 script.py",
            "sandbox_required": true,
            "sandbox_type": "none"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "sandbox_required");
        assert_eq!(response["next_action"], "deny_tool_execution");
    }

    #[test]
    fn local_command_allows_with_limits() {
        let response = execution_plan_command(&json!({
            "command": "python3 script.py",
            "max_runtime_seconds": 999999,
            "memory": "1g"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_action"], "allow_tool_execution");
        assert_eq!(response["execution"]["network"], "none");
        assert_eq!(
            response["execution"]["max_runtime_seconds"],
            MAX_RUNTIME_SECONDS
        );
        assert_eq!(response["execution"]["cap_drop"], "ALL");
    }

    #[test]
    fn network_command_requires_approval() {
        let response = execution_plan_command(&json!({
            "command": "curl https://example.com",
            "sandbox_type": "docker"
        }));
        assert_eq!(response["decision"], "require_approval");
        assert_eq!(response["reason"], "network_execution_requires_approval");
        assert_eq!(response["approval_required"], true);
        assert_eq!(response["next_action"], "request_tool_execution_approval");
    }
}

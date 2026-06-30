use crate::protocol;
use serde_json::{json, Value};
use std::collections::BTreeSet;

const POLICY_KEYS: &[&str] = &[
    "policy_id",
    "policy_version",
    "autonomy_mode",
    "allowed_capabilities",
    "approval_required_capabilities",
    "blocked_capabilities",
    "domain_allowlist",
    "filesystem_scope",
    "blocked_filesystem_scope",
    "approval_ttl_seconds",
    "sandbox_type",
];

#[derive(Debug, Clone)]
pub struct Policy {
    pub id: Option<String>,
    pub version: Option<String>,
    pub autonomy_mode: String,
    pub allowed_capabilities: BTreeSet<String>,
    pub approval_required_capabilities: BTreeSet<String>,
    pub blocked_capabilities: BTreeSet<String>,
    pub domain_allowlist: Vec<String>,
    pub filesystem_scope: Vec<String>,
    pub blocked_filesystem_scope: Vec<String>,
    pub sandbox_type: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PolicyDecisionKind {
    Allow,
    RequireApproval,
    Block,
}

#[derive(Debug, Clone)]
pub struct PolicyDecision {
    pub kind: PolicyDecisionKind,
    pub reason: String,
}

impl PolicyDecision {
    pub fn decision_name(&self) -> &'static str {
        match self.kind {
            PolicyDecisionKind::Allow => "allow",
            PolicyDecisionKind::RequireApproval => "require_approval",
            PolicyDecisionKind::Block => "block",
        }
    }
}

impl Policy {
    pub fn from_input(input: &Value) -> Result<Self, String> {
        let policy_value =
            protocol::policy_payload(input).ok_or_else(|| "policy_missing".to_string())?;
        Self::from_value(policy_value)
    }

    pub fn from_value(policy_value: &Value) -> Result<Self, String> {
        if !policy_value.is_object() {
            return Err("policy_malformed".to_string());
        }
        if !protocol::object_has_any_key(policy_value, POLICY_KEYS) {
            return Err("policy_missing".to_string());
        }

        let autonomy_mode = normalize_autonomy_mode(
            protocol::string_field(policy_value, "autonomy_mode")
                .as_deref()
                .unwrap_or("ask_every_time"),
        );

        Ok(Self {
            id: protocol::string_field(policy_value, "policy_id"),
            version: protocol::string_field(policy_value, "policy_version"),
            autonomy_mode,
            allowed_capabilities: capability_set(policy_value, "allowed_capabilities"),
            approval_required_capabilities: capability_set(
                policy_value,
                "approval_required_capabilities",
            ),
            blocked_capabilities: capability_set(policy_value, "blocked_capabilities"),
            domain_allowlist: protocol::string_vec_field(policy_value, "domain_allowlist"),
            filesystem_scope: protocol::string_vec_field(policy_value, "filesystem_scope"),
            blocked_filesystem_scope: protocol::string_vec_field(
                policy_value,
                "blocked_filesystem_scope",
            ),
            sandbox_type: protocol::string_field(policy_value, "sandbox_type"),
        })
    }

    pub fn evaluate_capability(&self, capability: &str) -> PolicyDecision {
        self.evaluate_request(capability, None, None)
    }

    pub fn evaluate_request(
        &self,
        capability: &str,
        requested_domain: Option<&str>,
        requested_path: Option<&str>,
    ) -> PolicyDecision {
        let Some(normalized_capability) = normalize_capability(capability) else {
            return PolicyDecision {
                kind: PolicyDecisionKind::Block,
                reason: "unknown_capability".to_string(),
            };
        };

        if self.blocked_capabilities.contains(&normalized_capability) {
            return PolicyDecision {
                kind: PolicyDecisionKind::Block,
                reason: "capability_blocked_by_policy".to_string(),
            };
        }

        if let Some(domain) = requested_domain {
            if !self.domain_allowed(domain) {
                return PolicyDecision {
                    kind: PolicyDecisionKind::Block,
                    reason: "domain_outside_policy_scope".to_string(),
                };
            }
        }

        if let Some(path) = requested_path {
            if self.path_blocked(path) {
                return PolicyDecision {
                    kind: PolicyDecisionKind::Block,
                    reason: "path_blocked_by_policy_scope".to_string(),
                };
            }
            if !self.path_allowed(path) {
                return PolicyDecision {
                    kind: PolicyDecisionKind::Block,
                    reason: "path_outside_policy_scope".to_string(),
                };
            }
        }

        if self.autonomy_mode == "yolo" {
            return PolicyDecision {
                kind: PolicyDecisionKind::Allow,
                reason: "yolo_policy_fast_path".to_string(),
            };
        }

        if self.allowed_capabilities.contains(&normalized_capability) {
            return PolicyDecision {
                kind: PolicyDecisionKind::Allow,
                reason: "capability_allowed_by_policy".to_string(),
            };
        }

        if self
            .approval_required_capabilities
            .contains(&normalized_capability)
        {
            return PolicyDecision {
                kind: PolicyDecisionKind::RequireApproval,
                reason: "capability_requires_approval".to_string(),
            };
        }

        match self.autonomy_mode.as_str() {
            "read_only" if is_read_only_capability(&normalized_capability) => PolicyDecision {
                kind: PolicyDecisionKind::Allow,
                reason: "read_only_capability_allowed".to_string(),
            },
            "cautious" if is_read_only_capability(&normalized_capability) => PolicyDecision {
                kind: PolicyDecisionKind::Allow,
                reason: "cautious_read_capability_allowed".to_string(),
            },
            "cautious" | "ask_every_time" | "safe_autopilot" | "trusted_workstation" => {
                PolicyDecision {
                    kind: PolicyDecisionKind::RequireApproval,
                    reason: "default_approval_required".to_string(),
                }
            }
            _ => PolicyDecision {
                kind: PolicyDecisionKind::Block,
                reason: "default_deny".to_string(),
            },
        }
    }

    pub fn domain_allowed(&self, requested_domain: &str) -> bool {
        let domain = requested_domain
            .trim()
            .trim_start_matches("https://")
            .trim_start_matches("http://")
            .trim_end_matches('/')
            .to_ascii_lowercase();
        if domain.is_empty() {
            return false;
        }
        self.domain_allowlist.iter().any(|entry| {
            let normalized = entry
                .trim()
                .trim_start_matches("https://")
                .trim_start_matches("http://")
                .trim_end_matches('/')
                .to_ascii_lowercase();
            normalized == "*" || domain == normalized || domain.ends_with(&format!(".{normalized}"))
        })
    }

    pub fn path_allowed(&self, requested_path: &str) -> bool {
        let path = requested_path.trim();
        if path.is_empty() {
            return false;
        }
        self.filesystem_scope
            .iter()
            .any(|entry| entry.trim() == "*" || path.starts_with(entry.trim()))
    }

    pub fn path_blocked(&self, requested_path: &str) -> bool {
        let path = requested_path.trim();
        self.blocked_filesystem_scope
            .iter()
            .any(|entry| entry.trim() == "*" || path.starts_with(entry.trim()))
    }

    pub fn to_json(&self) -> Value {
        json!({
            "policy_id": self.id,
            "policy_version": self.version,
            "autonomy_mode": self.autonomy_mode,
            "allowed_capabilities": self.allowed_capabilities,
            "approval_required_capabilities": self.approval_required_capabilities,
            "blocked_capabilities": self.blocked_capabilities,
            "domain_allowlist": self.domain_allowlist,
            "filesystem_scope": self.filesystem_scope,
            "blocked_filesystem_scope": self.blocked_filesystem_scope,
            "sandbox_type": self.sandbox_type,
        })
    }
}

pub fn validate_policy_command(input: &Value) -> Value {
    let policy = match Policy::from_input(input) {
        Ok(policy) => policy,
        Err(reason) => return protocol::block(&reason),
    };

    let Some(capability) = protocol::string_field(input, "capability") else {
        return json!({
            "ok": true,
            "decision": "allow",
            "reason": "policy_valid",
            "next_action": "validate_agent_computer_policy",
            "policy": policy.to_json(),
        });
    };

    let requested_domain = protocol::string_field(input, "requested_domain");
    let requested_path = protocol::string_field(input, "requested_path");
    let decision = policy.evaluate_request(
        &capability,
        requested_domain.as_deref(),
        requested_path.as_deref(),
    );
    let next_action = match decision.decision_name() {
        "allow" => "allow_agent_computer_request",
        "require_approval" => "request_agent_computer_approval",
        _ => "deny_agent_computer_request",
    };
    json!({
        "ok": decision.kind != PolicyDecisionKind::Block,
        "decision": decision.decision_name(),
        "reason": decision.reason,
        "next_action": next_action,
        "capability": normalize_capability(&capability),
        "requested_domain": requested_domain,
        "requested_path": requested_path,
        "policy": policy.to_json(),
    })
}

pub fn normalize_autonomy_mode(value: &str) -> String {
    let normalized = normalize_token(value);
    match normalized.as_str() {
        "yolo" | "autonomous" | "full_autonomy" | "maximum_autonomy" => "yolo".to_string(),
        "cautious" | "approval_required" | "ask_for_writes" => "cautious".to_string(),
        "deny_all" | "lockdown" | "disabled" | "off" | "emergency_stop" | "stop" | "killed" => {
            "deny_all".to_string()
        }
        "read_only" | "readonly" => "read_only".to_string(),
        "safe_autopilot" | "safe_auto" => "safe_autopilot".to_string(),
        "trusted_workstation" | "trusted" => "trusted_workstation".to_string(),
        "ask_every_time" | "ask" | "manual" => "ask_every_time".to_string(),
        _ => "ask_every_time".to_string(),
    }
}

pub fn normalize_capability(value: &str) -> Option<String> {
    let normalized = normalize_token(value);
    let canonical = match normalized.as_str() {
        "read_file"
        | "read_files"
        | "file_read"
        | "filesystem_read"
        | "filesystem_read_only"
        | "connector_action_read"
        | "connector_read"
        | "browser_read"
        | "screen_read"
        | "file_metadata"
        | "memory_read"
        | "vision_screen" => "read_files",
        "write_file"
        | "write_files"
        | "file_write"
        | "filesystem_write"
        | "filesystem_read_write"
        | "memory_write" => "write_files",
        "shell" | "shell_command" | "shell_execute" | "command" | "terminal" | "exec"
        | "execute" => "shell_command",
        "terminal_command" | "terminal_approved_script" => "shell_command",
        "network" | "network_access" | "http" | "web_request" | "cloud_storage_access" => {
            "network_access"
        }
        "browser"
        | "browser_control"
        | "web_browser"
        | "browser_click"
        | "browser_form_submit"
        | "browser_automation"
        | "browser_automation_interactive" => "browser_control",
        "mcp" | "mcp_server" | "tool_server" => "mcp_server",
        "long_running" | "long_running_task" | "background_task" | "scheduler_create" => {
            "long_running_task"
        }
        "process" | "process_control" | "kill_process" | "app_control" => "process_control",
        "git" | "git_write" | "git_push" | "git_commit" | "production_deploy" => "git_write",
        "secret" | "secrets" | "secret_access" | "credential_access" => "secret_access",
        "external_write"
        | "external_side_effect"
        | "send_email"
        | "calendar_write"
        | "communication_send"
        | "communication_draft"
        | "send_message"
        | "post_message"
        | "reply_message"
        | "notification_send"
        | "commerce_payment" => "external_write",
        "destructive" | "destructive_action" | "delete" | "delete_data" | "file_delete"
        | "delete_files" | "delete_records" | "transfer_funds" | "payment_transfer" => {
            "destructive_action"
        }
        "system"
        | "system_control"
        | "os_control"
        | "install_software"
        | "install_extension"
        | "system_change_setting"
        | "system_change_permission" => "system_control",
        _ => return None,
    };
    Some(canonical.to_string())
}

pub fn normalize_token(value: &str) -> String {
    value
        .trim()
        .to_ascii_lowercase()
        .chars()
        .map(|ch| match ch {
            '-' | ' ' | '/' | '.' => '_',
            other => other,
        })
        .collect::<String>()
        .split('_')
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join("_")
}

pub fn is_read_only_capability(capability: &str) -> bool {
    matches!(capability, "read_files")
}

fn capability_set(input: &Value, key: &str) -> BTreeSet<String> {
    protocol::string_vec_field(input, key)
        .into_iter()
        .filter_map(|value| normalize_capability(&value))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_policy_blocks() {
        let response = validate_policy_command(&json!({}));
        assert_eq!(response["ok"], false);
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "policy_missing");
    }

    #[test]
    fn yolo_allows_known_capability() {
        let response = validate_policy_command(&json!({
            "policy": {"policy_id": "p1", "autonomy_mode": "yolo"},
            "capability": "shell"
        }));
        assert_eq!(response["ok"], true);
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["reason"], "yolo_policy_fast_path");
        assert_eq!(response["next_action"], "allow_agent_computer_request");
    }

    #[test]
    fn ask_every_time_requires_approval_for_shell() {
        let response = validate_policy_command(&json!({
            "policy": {"policy_id": "p1", "autonomy_mode": "ask_every_time"},
            "capability": "shell"
        }));
        assert_eq!(response["ok"], true);
        assert_eq!(response["decision"], "require_approval");
        assert_eq!(response["next_action"], "request_agent_computer_approval");
    }

    #[test]
    fn explicit_block_wins_before_yolo() {
        let response = validate_policy_command(&json!({
            "policy": {
                "policy_id": "p1",
                "autonomy_mode": "yolo",
                "blocked_capabilities": ["shell_command"]
            },
            "capability": "shell"
        }));
        assert_eq!(response["ok"], false);
        assert_eq!(response["decision"], "block");
    }

    #[test]
    fn requested_domain_must_be_inside_allowlist() {
        let response = validate_policy_command(&json!({
            "policy": {
                "policy_id": "p1",
                "autonomy_mode": "yolo",
                "domain_allowlist": ["example.com"]
            },
            "capability": "network_access",
            "requested_domain": "evil.test"
        }));
        assert_eq!(response["ok"], false);
        assert_eq!(response["reason"], "domain_outside_policy_scope");
    }

    #[test]
    fn requested_path_must_be_inside_filesystem_scope() {
        let response = validate_policy_command(&json!({
            "policy": {
                "policy_id": "p1",
                "autonomy_mode": "yolo",
                "filesystem_scope": ["/workspace"],
                "blocked_filesystem_scope": ["/workspace/secrets"]
            },
            "capability": "read_files",
            "requested_path": "/tmp/outside"
        }));
        assert_eq!(response["ok"], false);
        assert_eq!(response["reason"], "path_outside_policy_scope");
    }

    #[test]
    fn blocked_path_wins_inside_filesystem_scope() {
        let response = validate_policy_command(&json!({
            "policy": {
                "policy_id": "p1",
                "autonomy_mode": "yolo",
                "filesystem_scope": ["/workspace"],
                "blocked_filesystem_scope": ["/workspace/secrets"]
            },
            "capability": "read_files",
            "requested_path": "/workspace/secrets/key.txt"
        }));
        assert_eq!(response["ok"], false);
        assert_eq!(response["reason"], "path_blocked_by_policy_scope");
    }
}

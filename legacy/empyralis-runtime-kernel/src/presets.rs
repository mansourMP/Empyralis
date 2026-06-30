use crate::policy::{normalize_token, Policy};
use crate::protocol;
use serde_json::{json, Value};

const ALL_CAPABILITIES: &[&str] = &[
    "app.control",
    "browser.click",
    "browser.form_submit",
    "browser.read",
    "cloud_storage.access",
    "commerce.payment",
    "communication.draft",
    "communication.send",
    "credential.access",
    "file.delete",
    "file.metadata",
    "file.read",
    "file.write",
    "install.extension",
    "install.software",
    "memory.read",
    "memory.write",
    "notification.send",
    "production.deploy",
    "scheduler.create",
    "screen.read",
    "system.change_permission",
    "system.change_setting",
    "terminal.approved_script",
    "terminal.command",
    "vision.screen",
];

const WRITE_OR_PRIVILEGED_CAPABILITIES: &[&str] = &[
    "browser.click",
    "browser.form_submit",
    "cloud_storage.access",
    "communication.send",
    "file.read",
    "file.write",
    "memory.write",
    "scheduler.create",
    "terminal.command",
];

const SAFE_READ_PLUS_DRAFT_CAPABILITIES: &[&str] = &[
    "browser.read",
    "communication.draft",
    "file.metadata",
    "file.read",
    "memory.read",
    "notification.send",
    "screen.read",
    "vision.screen",
];

const CAUTIOUS_BLOCKED_CAPABILITIES: &[&str] = &[
    "app.control",
    "commerce.payment",
    "credential.access",
    "file.delete",
    "install.extension",
    "install.software",
    "production.deploy",
    "system.change_permission",
    "system.change_setting",
    "terminal.approved_script",
];

pub fn policy_preset_command(input: &Value) -> Value {
    let Some(raw_preset) = protocol::string_field(input, "preset")
        .or_else(|| protocol::string_field(input, "name"))
        .or_else(|| protocol::string_field(input, "policy_preset"))
    else {
        return protocol::block("policy_preset_missing");
    };

    let Some(preset_name) = normalize_preset_name(&raw_preset) else {
        return protocol::block("unknown_policy_preset");
    };

    let policy = match preset_name.as_str() {
        "yolo" => yolo_preset(),
        "cautious" => cautious_preset(),
        "deny_all" => deny_all_preset(),
        _ => return protocol::block("unknown_policy_preset"),
    };

    json!({
        "ok": true,
        "decision": "allow",
        "reason": "policy_preset_resolved",
        "next_action": "apply_policy_preset",
        "preset": preset_name,
        "policy": policy,
    })
}

pub fn normalize_preset_name(value: &str) -> Option<String> {
    let normalized = normalize_token(value);
    let preset = match normalized.as_str() {
        "yolo" | "full_send" | "maximum_autonomy" | "max_autonomy" | "autonomous" | "fast" => {
            "yolo"
        }
        "cautious" | "safe" | "safe_default" | "default" | "ask_for_writes" | "careful" => {
            "cautious"
        }
        "deny_all" | "deny" | "lockdown" | "locked_down" | "off" | "disabled" | "no_access" => {
            "deny_all"
        }
        _ => return None,
    };
    Some(preset.to_string())
}

fn yolo_preset() -> Value {
    json!({
        "policy_id": "preset:yolo",
        "policy_version": 1,
        "autonomy_mode": "yolo",
        "allowed_capabilities": ALL_CAPABILITIES,
        "approval_required_capabilities": [],
        "blocked_capabilities": [],
        "domain_allowlist": ["*"],
        "filesystem_scope": ["*"],
        "blocked_filesystem_scope": [],
        "sandbox_type": "docker",
        "emergency_stop_enabled": true,
    })
}

fn cautious_preset() -> Value {
    json!({
        "policy_id": "preset:cautious",
        "policy_version": 1,
        "autonomy_mode": "safe_autopilot",
        "allowed_capabilities": SAFE_READ_PLUS_DRAFT_CAPABILITIES,
        "approval_required_capabilities": WRITE_OR_PRIVILEGED_CAPABILITIES,
        "blocked_capabilities": CAUTIOUS_BLOCKED_CAPABILITIES,
        "domain_allowlist": [],
        "filesystem_scope": [],
        "blocked_filesystem_scope": [],
        "sandbox_type": "docker",
        "emergency_stop_enabled": true,
    })
}

fn deny_all_preset() -> Value {
    json!({
        "policy_id": "preset:deny_all",
        "policy_version": 1,
        "autonomy_mode": "emergency_stop",
        "allowed_capabilities": [],
        "approval_required_capabilities": [],
        "blocked_capabilities": ALL_CAPABILITIES,
        "domain_allowlist": [],
        "filesystem_scope": [],
        "blocked_filesystem_scope": ["*"],
        "sandbox_type": "none",
        "emergency_stop_enabled": true,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolves_yolo_aliases() {
        let response = policy_preset_command(&json!({"preset": "full-send"}));
        assert_eq!(response["ok"], true);
        assert_eq!(response["next_action"], "apply_policy_preset");
        assert_eq!(response["preset"], "yolo");
        assert_eq!(response["policy"]["autonomy_mode"], "yolo");
    }

    #[test]
    fn cautious_allows_reads_and_requires_write_approval() {
        let response = policy_preset_command(&json!({"preset": "safe"}));
        let policy = Policy::from_value(&response["policy"]).unwrap();
        assert_eq!(
            policy.evaluate_capability("read_files").decision_name(),
            "allow"
        );
        assert_eq!(
            policy.evaluate_capability("write_files").decision_name(),
            "require_approval"
        );
    }

    #[test]
    fn deny_all_blocks_shell() {
        let response = policy_preset_command(&json!({"preset": "lockdown"}));
        let policy = Policy::from_value(&response["policy"]).unwrap();
        assert_eq!(policy.evaluate_capability("shell").decision_name(), "block");
    }

    #[test]
    fn unknown_preset_blocks() {
        let response = policy_preset_command(&json!({"preset": "unsafe-custom"}));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "unknown_policy_preset");
    }
}

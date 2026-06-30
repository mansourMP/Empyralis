use crate::policy::{normalize_capability, normalize_token, Policy, PolicyDecisionKind};
use crate::protocol;
use serde_json::{json, Value};
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
enum RiskLevel {
    Low,
    Medium,
    High,
    Critical,
}

impl RiskLevel {
    fn as_str(self) -> &'static str {
        match self {
            RiskLevel::Low => "low",
            RiskLevel::Medium => "medium",
            RiskLevel::High => "high",
            RiskLevel::Critical => "critical",
        }
    }
}

pub fn classify_risk_command(input: &Value) -> Value {
    if let Some(kill_state) = current_kill_state(input) {
        return risk_response(
            input,
            "block",
            RiskLevel::Critical,
            "kill_state_active",
            Some(format!("kill_state:{kill_state}")),
            None,
        );
    }

    if let Some(health_state) = computer_profile_health_state(input) {
        return risk_response(
            input,
            "block",
            RiskLevel::Critical,
            "computer_profile_unhealthy",
            Some(format!("profile_{health_state}")),
            None,
        );
    }

    let policy = match Policy::from_input(input) {
        Ok(policy) => policy,
        Err(reason) => {
            return risk_response(
                input,
                "block",
                RiskLevel::Critical,
                &reason,
                Some(reason.clone()),
                None,
            );
        }
    };

    let capability_input = protocol::string_field(input, "capability").unwrap_or_default();
    let Some(capability) = normalize_capability(&capability_input) else {
        return risk_response(
            input,
            "block",
            RiskLevel::Critical,
            "unknown_capability",
            Some("unknown_capability".to_string()),
            None,
        );
    };

    let requested_domain = protocol::string_field(input, "requested_domain");
    let requested_path = protocol::string_field(input, "requested_path");
    let policy_decision = policy.evaluate_request(
        &capability,
        requested_domain.as_deref(),
        requested_path.as_deref(),
    );
    if policy_decision.kind == PolicyDecisionKind::Block {
        return risk_response(
            input,
            "block",
            RiskLevel::Critical,
            &policy_decision.reason,
            Some(normalize_blocked_reason(&policy_decision.reason)),
            Some(capability),
        );
    }

    if policy.autonomy_mode == "yolo" {
        return risk_response(
            input,
            "allow",
            RiskLevel::Low,
            "yolo_policy_fast_path",
            None,
            Some(capability),
        );
    }

    let action_class = action_class(input);
    let payload_text = payload_text(input);
    let risk_level = risk_for_capability(&capability, &action_class, &payload_text);
    let decision = if policy_decision.kind == PolicyDecisionKind::RequireApproval
        || matches!(risk_level, RiskLevel::High | RiskLevel::Critical)
    {
        "require_approval"
    } else {
        "allow"
    };

    risk_response(
        input,
        decision,
        risk_level,
        if decision == "allow" {
            "risk_allowed"
        } else {
            "risk_requires_approval"
        },
        None,
        Some(capability),
    )
}

fn risk_response(
    input: &Value,
    decision: &str,
    risk_level: RiskLevel,
    reason: &str,
    blocked_reason: Option<String>,
    capability: Option<String>,
) -> Value {
    let action_class = action_class(input);
    let capability = capability.or_else(|| protocol::string_field(input, "capability"));
    let approval_scopes_required = if decision == "require_approval" {
        json!([
            capability
                .clone()
                .unwrap_or_else(|| "unknown_capability".to_string()),
            risk_level.as_str()
        ])
    } else {
        json!([])
    };

    json!({
        "ok": decision != "block",
        "decision_id": decision_id(),
        "policy_version": policy_version(input),
        "risk_level": risk_level.as_str(),
        "risk_class": risk_level.as_str(),
        "action_class": action_class,
        "capability": capability,
        "target_summary": target_summary(input),
        "decision": decision,
        "reason": reason,
        "next_action": match decision {
            "allow" => "allow_capability_execution",
            "require_approval" => "request_capability_risk_approval",
            _ => "block_capability_execution",
        },
        "approval_required": decision == "require_approval",
        "approval_scopes_required": approval_scopes_required,
        "blocked_reason": blocked_reason,
        "audit_visibility": audit_visibility(decision, risk_level),
        "recording_required": recording_required(decision, risk_level),
        "retention_class": retention_class(risk_level),
        "cacheable": decision == "allow" && risk_level == RiskLevel::Low,
    })
}

fn action_class(input: &Value) -> String {
    let value =
        protocol::string_field(input, "action_class").unwrap_or_else(|| "unknown".to_string());
    match normalize_token(&value).as_str() {
        "read" | "inspect" | "list" => "read".to_string(),
        "write" | "edit" | "create" | "update" => "write".to_string(),
        "execute" | "exec" | "run" | "shell" => "execute".to_string(),
        "network" | "http" | "external" => "network".to_string(),
        "browser" | "click" | "type" => "browser".to_string(),
        "delete" | "remove" | "destructive" => "delete".to_string(),
        "secret" | "credential" | "token" => "secret".to_string(),
        "system" | "os" | "process" => "system_control".to_string(),
        _ => "unknown".to_string(),
    }
}

fn risk_for_capability(capability: &str, action_class: &str, payload_text: &str) -> RiskLevel {
    let mut risk = match capability {
        "system_control" | "process_control" | "secret_access" | "destructive_action" => {
            RiskLevel::Critical
        }
        "shell_command" | "write_files" | "git_write" | "external_write" | "mcp_server" => {
            RiskLevel::High
        }
        "network_access" | "browser_control" | "long_running_task" => RiskLevel::Medium,
        _ => RiskLevel::Low,
    };

    if matches!(action_class, "delete" | "secret" | "system_control") {
        risk = risk.max(RiskLevel::Critical);
    }
    if matches!(action_class, "write" | "execute" | "network" | "browser") {
        risk = risk.max(RiskLevel::Medium);
    }
    if contains_secret_marker(payload_text) {
        risk = risk.max(RiskLevel::Critical);
    }
    if contains_destructive_marker(payload_text) {
        risk = risk.max(RiskLevel::High);
    }
    risk
}

fn payload_text(input: &Value) -> String {
    let payload = input.get("payload").unwrap_or(input);
    let mut parts = Vec::new();
    flatten_json(payload, &mut parts, 4096);
    parts.join(" ").to_ascii_lowercase()
}

fn flatten_json(value: &Value, parts: &mut Vec<String>, budget: usize) {
    if parts.iter().map(String::len).sum::<usize>() > budget {
        return;
    }
    match value {
        Value::String(value) => parts.push(value.clone()),
        Value::Array(items) => {
            for item in items {
                flatten_json(item, parts, budget);
            }
        }
        Value::Object(object) => {
            for (key, value) in object {
                parts.push(key.clone());
                flatten_json(value, parts, budget);
            }
        }
        other => parts.push(other.to_string()),
    }
}

fn contains_secret_marker(text: &str) -> bool {
    [
        "api_key", "api-key", "bearer ", "sk-", "sk_ant", "sk-ant-", "ghp_", "token", "password",
    ]
    .iter()
    .any(|marker| text.contains(marker))
}

fn contains_destructive_marker(text: &str) -> bool {
    [
        "rm -rf",
        "drop table",
        "delete all",
        "format disk",
        "shutdown",
        "reboot",
        "wipe",
    ]
    .iter()
    .any(|marker| text.contains(marker))
}

fn current_kill_state(input: &Value) -> Option<String> {
    let state = normalize_token(&protocol::string_field(input, "current_kill_state")?);
    if matches!(
        state.as_str(),
        "active"
            | "triggered"
            | "engaged"
            | "killed"
            | "kill"
            | "paused"
            | "suspended"
            | "emergency_stop"
            | "workspace_emergency_stop"
            | "locked_down"
    ) {
        Some(state)
    } else {
        None
    }
}

fn computer_profile_health_state(input: &Value) -> Option<String> {
    let state = input
        .get("computer_profile")
        .and_then(|profile| protocol::string_field(profile, "health_state"))
        .map(|state| normalize_token(&state))?;
    if matches!(
        state.as_str(),
        "unhealthy" | "offline" | "disabled" | "locked" | "failed"
    ) {
        Some(state)
    } else {
        None
    }
}

fn normalize_blocked_reason(reason: &str) -> String {
    match reason {
        "domain_outside_policy_scope" => "domain_not_allowed".to_string(),
        "path_outside_policy_scope" | "path_blocked_by_policy_scope" => {
            "filesystem_scope_not_allowed".to_string()
        }
        "unknown_capability" => "policy_blocked".to_string(),
        other => other.to_string(),
    }
}

fn policy_version(input: &Value) -> Option<String> {
    protocol::policy_payload(input)
        .and_then(|policy| protocol::string_field(policy, "policy_version"))
}

fn target_summary(input: &Value) -> String {
    protocol::string_field(input, "target_summary")
        .or_else(|| protocol::string_field(input, "target"))
        .or_else(|| protocol::string_field(input, "requested_domain"))
        .or_else(|| protocol::string_field(input, "requested_path"))
        .unwrap_or_else(|| "unspecified_target".to_string())
}

fn audit_visibility(decision: &str, risk_level: RiskLevel) -> &'static str {
    if decision == "block" || matches!(risk_level, RiskLevel::High | RiskLevel::Critical) {
        "security"
    } else {
        "standard"
    }
}

fn recording_required(decision: &str, risk_level: RiskLevel) -> bool {
    decision == "block" || matches!(risk_level, RiskLevel::High | RiskLevel::Critical)
}

fn retention_class(risk_level: RiskLevel) -> &'static str {
    match risk_level {
        RiskLevel::Low => "short",
        RiskLevel::Medium | RiskLevel::High => "standard",
        RiskLevel::Critical => "extended",
    }
}

fn decision_id() -> String {
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or_default();
    format!("crd_rust_{millis}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn yolo_fast_path_is_low_allow() {
        let response = classify_risk_command(&json!({
            "policy": {"policy_id": "p1", "autonomy_mode": "yolo"},
            "capability": "shell_command",
            "payload": {"command": "echo safe"}
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["risk_level"], "low");
        assert_eq!(response["next_action"], "allow_capability_execution");
    }

    #[test]
    fn secret_payload_is_critical() {
        let response = classify_risk_command(&json!({
            "policy": {"policy_id": "p1", "autonomy_mode": "cautious"},
            "capability": "network_access",
            "payload": {"headers": {"authorization": "Bearer sk-test"}}
        }));
        assert_eq!(response["decision"], "require_approval");
        assert_eq!(response["risk_level"], "critical");
        assert_eq!(response["next_action"], "request_capability_risk_approval");
    }

    #[test]
    fn kill_state_blocks() {
        let response = classify_risk_command(&json!({
            "current_kill_state": "active",
            "policy": {"policy_id": "p1", "autonomy_mode": "yolo"},
            "capability": "read_files"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["blocked_reason"], "kill_state:active");
        assert_eq!(response["next_action"], "block_capability_execution");
    }

    #[test]
    fn yolo_risk_fast_path_does_not_bypass_domain_scope() {
        let response = classify_risk_command(&json!({
            "policy": {
                "policy_id": "p1",
                "autonomy_mode": "yolo",
                "domain_allowlist": ["example.com"]
            },
            "capability": "network_access",
            "requested_domain": "evil.test"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["blocked_reason"], "domain_not_allowed");
    }
}

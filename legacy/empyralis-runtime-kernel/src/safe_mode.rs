use crate::policy::normalize_token;
use crate::protocol;
use serde_json::{json, Value};

const UNSAFE_PREFIXES: &[&str] = &["computer_control", "browser_automation"];
const UNSAFE_EXACT: &[&str] = &["shell.execute", "shell_execute", "shell_command"];
const SAFE_MODE_BLOCKED_CAPABILITIES: &[&str] = &[
    "computer_control",
    "computer_control.*",
    "browser_automation.interactive",
    "browser_automation.*",
    "shell.execute",
];

pub fn safe_mode_decision_command(input: &Value) -> Value {
    let capability = protocol::string_field(input, "capability")
        .or_else(|| protocol::string_field(input, "capability_id"));
    let controlling_match = controlling_match(input);
    let safe_mode_enabled =
        boolish(input, "safe_mode_enabled") || boolish(input, "forced_safe_mode");
    let maintenance_mode = boolish(input, "maintenance_mode");
    let kill_switch_enabled = boolish(input, "kill_switch_enabled");
    let incident_mode = normalize_incident_mode(
        protocol::string_field(input, "incident_mode")
            .as_deref()
            .unwrap_or("active"),
    );
    let health_state = normalize_token(
        protocol::string_field(input, "health_state")
            .or_else(|| {
                input
                    .get("computer_profile")
                    .and_then(|profile| protocol::string_field(profile, "health_state"))
            })
            .as_deref()
            .unwrap_or("healthy"),
    );

    if kill_switch_enabled {
        return decision(
            "block",
            "kill_switch_enabled",
            capability,
            controlling_match.as_ref(),
            safe_mode_enabled,
            maintenance_mode,
            &incident_mode,
            &health_state,
        );
    }

    if matches!(
        health_state.as_str(),
        "unhealthy" | "offline" | "disabled" | "locked" | "failed"
    ) {
        return decision(
            "block",
            "computer_profile_unhealthy",
            capability,
            controlling_match.as_ref(),
            safe_mode_enabled,
            maintenance_mode,
            &incident_mode,
            &health_state,
        );
    }

    match incident_mode.as_str() {
        "reject" => {
            return decision(
                "block",
                "incident_reject_mode",
                capability,
                controlling_match.as_ref(),
                safe_mode_enabled,
                maintenance_mode,
                &incident_mode,
                &health_state,
            )
        }
        "pause" => {
            return decision(
                "block",
                "incident_pause_mode",
                capability,
                controlling_match.as_ref(),
                safe_mode_enabled,
                maintenance_mode,
                &incident_mode,
                &health_state,
            )
        }
        "drain" => {
            return decision(
                "degrade",
                "incident_drain_mode",
                capability,
                controlling_match.as_ref(),
                safe_mode_enabled,
                maintenance_mode,
                &incident_mode,
                &health_state,
            )
        }
        _ => {}
    }

    if safe_mode_enabled || maintenance_mode {
        if capability
            .as_deref()
            .map(is_unsafe_capability)
            .unwrap_or(false)
        {
            return decision(
                "block",
                "capability_blocked_by_safe_mode",
                capability,
                controlling_match.as_ref(),
                safe_mode_enabled,
                maintenance_mode,
                &incident_mode,
                &health_state,
            );
        }
        return decision(
            "degrade",
            "safe_mode_active",
            capability,
            controlling_match.as_ref(),
            safe_mode_enabled,
            maintenance_mode,
            &incident_mode,
            &health_state,
        );
    }

    decision(
        "allow",
        "safe_mode_clear",
        capability,
        controlling_match.as_ref(),
        safe_mode_enabled,
        maintenance_mode,
        &incident_mode,
        &health_state,
    )
}

struct ControllingMatch {
    control_type: Option<String>,
    control_scope: Option<String>,
    control_reason: Option<String>,
}

fn decision(
    decision: &str,
    reason: &str,
    capability: Option<String>,
    controlling_match: Option<&ControllingMatch>,
    safe_mode_enabled: bool,
    maintenance_mode: bool,
    incident_mode: &str,
    health_state: &str,
) -> Value {
    let next_action = match decision {
        "allow" => "allow_safe_mode_capability",
        "degrade" => "degrade_safe_mode_capability",
        _ => "disable_safe_mode_capability",
    };
    json!({
        "ok": decision != "block",
        "decision": decision,
        "reason": reason,
        "next_action": next_action,
        "capability": capability,
        "safe_mode_active": safe_mode_enabled || maintenance_mode,
        "maintenance_mode": maintenance_mode,
        "incident_mode": incident_mode,
        "health_state": health_state,
        "control_type": controlling_match.and_then(|value| value.control_type.clone()),
        "control_scope": controlling_match.and_then(|value| value.control_scope.clone()),
        "control_reason": controlling_match.and_then(|value| value.control_reason.clone()),
        "blocked_capabilities": SAFE_MODE_BLOCKED_CAPABILITIES,
        "runtime_mode": if decision == "allow" { "normal" } else { "safe" },
        "audit_visibility": if decision == "allow" { "standard" } else { "security" },
        "cacheable": decision == "allow",
    })
}

fn controlling_match(input: &Value) -> Option<ControllingMatch> {
    let chain = input.get("matched_chain")?.as_array()?;
    let last = chain.last()?.as_object()?;
    Some(ControllingMatch {
        control_type: last
            .get("type")
            .and_then(Value::as_str)
            .map(ToOwned::to_owned)
            .filter(|value| !value.trim().is_empty()),
        control_scope: last
            .get("scope")
            .and_then(Value::as_str)
            .map(ToOwned::to_owned)
            .filter(|value| !value.trim().is_empty()),
        control_reason: last
            .get("reason")
            .and_then(Value::as_str)
            .map(ToOwned::to_owned)
            .filter(|value| !value.trim().is_empty()),
    })
}

fn boolish(input: &Value, key: &str) -> bool {
    match input.get(key) {
        Some(Value::Bool(value)) => *value,
        Some(Value::String(value)) => matches!(
            normalize_token(value).as_str(),
            "1" | "true" | "yes" | "on" | "enabled" | "active"
        ),
        Some(Value::Number(value)) => value.as_u64().unwrap_or(0) > 0,
        _ => false,
    }
}

fn normalize_incident_mode(value: &str) -> String {
    match normalize_token(value).as_str() {
        "paused" => "pause".to_string(),
        "draining" => "drain".to_string(),
        "rejected" => "reject".to_string(),
        "pause" | "drain" | "reject" => normalize_token(value),
        _ => "active".to_string(),
    }
}

fn is_unsafe_capability(value: &str) -> bool {
    let token = normalize_token(value);
    if UNSAFE_EXACT.iter().any(|item| token == *item) {
        return true;
    }
    UNSAFE_PREFIXES
        .iter()
        .any(|prefix| token == *prefix || token.starts_with(&format!("{prefix}_")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clear_state_allows() {
        let response = safe_mode_decision_command(&json!({
            "capability": "read_files"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_action"], "allow_safe_mode_capability");
        assert_eq!(response["runtime_mode"], "normal");
    }

    #[test]
    fn kill_switch_blocks() {
        let response = safe_mode_decision_command(&json!({
            "kill_switch_enabled": true,
            "capability": "read_files"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "kill_switch_enabled");
    }

    #[test]
    fn safe_mode_blocks_unsafe_capability() {
        let response = safe_mode_decision_command(&json!({
            "safe_mode_enabled": true,
            "capability": "computer_control.open_app"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["next_action"], "disable_safe_mode_capability");
        assert_eq!(response["reason"], "capability_blocked_by_safe_mode");
    }

    #[test]
    fn drain_mode_degrades() {
        let response = safe_mode_decision_command(&json!({
            "incident_mode": "draining",
            "capability": "read_files"
        }));
        assert_eq!(response["decision"], "degrade");
        assert_eq!(response["next_action"], "degrade_safe_mode_capability");
        assert_eq!(response["reason"], "incident_drain_mode");
    }

    #[test]
    fn unhealthy_profile_blocks() {
        let response = safe_mode_decision_command(&json!({
            "computer_profile": {"health_state": "offline"},
            "capability": "read_files"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "computer_profile_unhealthy");
    }

    #[test]
    fn controlling_match_metadata_is_returned() {
        let response = safe_mode_decision_command(&json!({
            "kill_switch_enabled": true,
            "capability": "read_files",
            "matched_chain": [
                {"type": "kill_switch", "scope": "workspace", "reason": "workspace incident"}
            ]
        }));
        assert_eq!(response["control_type"], "kill_switch");
        assert_eq!(response["control_scope"], "workspace");
        assert_eq!(response["control_reason"], "workspace incident");
    }
}

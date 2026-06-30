use crate::policy::{normalize_capability, normalize_token};
use crate::protocol;
use serde_json::{json, Value};

pub fn approval_requirement_command(input: &Value) -> Value {
    let Some(capability_input) = protocol::string_field(input, "capability") else {
        return protocol::block("capability_missing");
    };
    let Some(capability) = normalize_capability(&capability_input) else {
        return protocol::block("unknown_capability");
    };

    let decision = normalize_decision(
        protocol::string_field(input, "decision")
            .as_deref()
            .unwrap_or("require_approval"),
    );
    let risk_level = normalize_risk_level(
        protocol::string_field(input, "risk_level")
            .or_else(|| protocol::string_field(input, "risk_class"))
            .as_deref()
            .unwrap_or("medium"),
    );
    let action_class = normalize_action_class(
        protocol::string_field(input, "action_class")
            .as_deref()
            .unwrap_or("unknown"),
    );
    let target_summary = protocol::string_field(input, "target_summary")
        .or_else(|| protocol::string_field(input, "target"))
        .or_else(|| protocol::string_field(input, "requested_domain"))
        .or_else(|| protocol::string_field(input, "requested_path"))
        .unwrap_or_else(|| "unspecified_target".to_string());

    if decision == "block" {
        return json!({
            "ok": false,
            "decision": "block",
            "reason": "approval_not_available_for_blocked_request",
            "approval_required": false,
            "next_action": "deny_approval_requirement",
            "capability": capability,
            "risk_level": risk_level,
            "action_class": action_class,
            "target_summary": target_summary,
            "approval": null,
            "audit_visibility": "security",
            "cacheable": false,
        });
    }

    if decision == "allow" && risk_level == "low" {
        return json!({
            "ok": true,
            "decision": "allow",
            "reason": "approval_not_required",
            "approval_required": false,
            "next_action": "skip_approval_requirement",
            "capability": capability,
            "risk_level": risk_level,
            "action_class": action_class,
            "target_summary": target_summary,
            "approval": null,
            "audit_visibility": "standard",
            "cacheable": true,
        });
    }

    let ttl_seconds = approval_ttl_seconds(&risk_level, &action_class);
    let approval_scope = approval_scope(&capability, &risk_level, &action_class, &target_summary);
    json!({
        "ok": true,
        "decision": "require_approval",
        "reason": "approval_required",
        "approval_required": true,
        "next_action": "request_owner_approval",
        "capability": capability,
        "risk_level": risk_level,
        "action_class": action_class,
        "target_summary": target_summary,
        "approval": {
            "scope": approval_scope,
            "ttl_seconds": ttl_seconds,
            "single_use": single_use_required(&risk_level, &action_class),
            "owner_visible": true,
        },
        "audit_visibility": audit_visibility(&risk_level),
        "cacheable": false,
    })
}

fn normalize_decision(value: &str) -> String {
    match normalize_token(value).as_str() {
        "allow" | "allowed" => "allow".to_string(),
        "require_approval" | "approval_required" | "ask" | "needs_approval" => {
            "require_approval".to_string()
        }
        "block" | "deny" | "denied" => "block".to_string(),
        _ => "require_approval".to_string(),
    }
}

fn normalize_risk_level(value: &str) -> String {
    match normalize_token(value).as_str() {
        "low" => "low".to_string(),
        "medium" => "medium".to_string(),
        "high" => "high".to_string(),
        "critical" => "critical".to_string(),
        _ => "medium".to_string(),
    }
}

fn normalize_action_class(value: &str) -> String {
    match normalize_token(value).as_str() {
        "read" | "inspect" | "list" => "read".to_string(),
        "write" | "edit" | "create" | "update" => "write".to_string(),
        "execute" | "exec" | "run" | "shell" => "execute".to_string(),
        "delete" | "remove" | "destructive" => "delete".to_string(),
        "secret" | "credential" | "token" => "secret".to_string(),
        "network" | "http" | "external" => "network".to_string(),
        "browser" | "click" | "type" => "browser".to_string(),
        other => other.to_string(),
    }
}

fn approval_ttl_seconds(risk_level: &str, action_class: &str) -> u64 {
    if matches!(risk_level, "critical") || matches!(action_class, "secret" | "delete") {
        60
    } else if risk_level == "high" || matches!(action_class, "execute" | "write") {
        300
    } else {
        900
    }
}

fn single_use_required(risk_level: &str, action_class: &str) -> bool {
    matches!(risk_level, "high" | "critical")
        || matches!(action_class, "secret" | "delete" | "execute")
}

fn approval_scope(
    capability: &str,
    risk_level: &str,
    action_class: &str,
    target_summary: &str,
) -> String {
    format!("{capability}:{action_class}:{risk_level}:{target_summary}")
}

fn audit_visibility(risk_level: &str) -> &'static str {
    if matches!(risk_level, "high" | "critical") {
        "security"
    } else {
        "standard"
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn low_allow_does_not_require_approval() {
        let response = approval_requirement_command(&json!({
            "decision": "allow",
            "capability": "read_files",
            "risk_level": "low",
            "action_class": "read"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["approval_required"], false);
        assert_eq!(response["cacheable"], true);
        assert_eq!(response["next_action"], "skip_approval_requirement");
    }

    #[test]
    fn high_execute_requires_single_use_approval() {
        let response = approval_requirement_command(&json!({
            "decision": "require_approval",
            "capability": "shell",
            "risk_level": "high",
            "action_class": "execute",
            "target_summary": "npm install"
        }));
        assert_eq!(response["decision"], "require_approval");
        assert_eq!(response["approval_required"], true);
        assert_eq!(response["approval"]["ttl_seconds"], 300);
        assert_eq!(response["approval"]["single_use"], true);
        assert_eq!(response["audit_visibility"], "security");
        assert_eq!(response["next_action"], "request_owner_approval");
    }

    #[test]
    fn critical_secret_approval_has_short_ttl() {
        let response = approval_requirement_command(&json!({
            "decision": "require_approval",
            "capability": "secret_access",
            "risk_level": "critical",
            "action_class": "secret"
        }));
        assert_eq!(response["approval"]["ttl_seconds"], 60);
        assert_eq!(response["approval"]["single_use"], true);
        assert_eq!(response["next_action"], "request_owner_approval");
    }

    #[test]
    fn blocked_request_does_not_create_approval() {
        let response = approval_requirement_command(&json!({
            "decision": "block",
            "capability": "destructive_action",
            "risk_level": "critical",
            "action_class": "delete"
        }));
        assert_eq!(response["ok"], false);
        assert_eq!(response["decision"], "block");
        assert_eq!(response["approval_required"], false);
        assert_eq!(response["next_action"], "deny_approval_requirement");
    }
}

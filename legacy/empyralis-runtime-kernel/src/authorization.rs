use crate::approvals;
use crate::policy::{normalize_capability, Policy, PolicyDecisionKind};
use crate::protocol;
use crate::risk;
use crate::safe_mode;
use serde_json::{json, Value};

pub fn authorize_request_command(input: &Value) -> Value {
    let policy = match Policy::from_input(input) {
        Ok(policy) => policy,
        Err(reason) => return authorization_block(&reason, None, None, None),
    };
    let Some(capability_input) = protocol::string_field(input, "capability") else {
        return authorization_block("capability_missing", None, None, None);
    };
    let Some(capability) = normalize_capability(&capability_input) else {
        return authorization_block("unknown_capability", None, None, None);
    };

    let requested_domain = protocol::string_field(input, "requested_domain");
    let requested_path = protocol::string_field(input, "requested_path");
    let safe_mode_output = safe_mode::safe_mode_decision_command(input);
    let safe_mode_decision =
        string_value(&safe_mode_output, "decision", "block").unwrap_or_else(|| "block".to_string());
    if safe_mode_decision == "block" {
        let reason = string_value(&safe_mode_output, "reason", "safe_mode_blocked")
            .unwrap_or_else(|| "safe_mode_blocked".to_string());
        return json!({
            "ok": false,
            "decision": "block",
            "reason": reason,
            "next_action": "deny_tool_execution",
            "capability": capability,
            "requested_domain": requested_domain,
            "requested_path": requested_path,
            "safe_mode": safe_mode_output,
            "policy_decision": null,
            "risk": null,
            "approval_required": false,
            "approval": null,
            "cacheable": false,
            "audit_visibility": "security",
        });
    }

    let context_gate = evaluate_policy_context(input);
    if let Some(reason) = context_gate.block_reason.clone() {
        return authorization_block(&reason, Some(capability), requested_domain, requested_path);
    }

    let policy_decision = policy.evaluate_request(
        &capability,
        requested_domain.as_deref(),
        requested_path.as_deref(),
    );
    if policy_decision.kind == PolicyDecisionKind::Block {
        return authorization_block(
            &policy_decision.reason,
            Some(capability),
            requested_domain,
            requested_path,
        );
    }

    let risk_output = risk::classify_risk_command(input);
    let risk_decision =
        string_value(&risk_output, "decision", "block").unwrap_or_else(|| "block".to_string());
    if risk_decision == "block" {
        let reason = string_value(&risk_output, "blocked_reason", "")
            .or_else(|| string_value(&risk_output, "reason", "risk_blocked"))
            .unwrap_or_else(|| "risk_blocked".to_string());
        return json!({
            "ok": false,
            "decision": "block",
            "reason": reason,
            "next_action": "deny_tool_execution",
            "capability": capability,
            "requested_domain": requested_domain,
            "requested_path": requested_path,
            "safe_mode": safe_mode_output,
            "policy_decision": {
                "decision": policy_decision.decision_name(),
                "reason": policy_decision.reason,
            },
            "risk": risk_output,
            "approval_required": false,
            "approval": null,
            "cacheable": false,
            "audit_visibility": "security",
        });
    }

    let action_class =
        protocol::string_field(input, "action_class").unwrap_or_else(|| "unknown".to_string());
    let target_summary = protocol::string_field(input, "target_summary")
        .or_else(|| protocol::string_field(input, "target"))
        .or_else(|| requested_domain.clone())
        .or_else(|| requested_path.clone())
        .unwrap_or_else(|| "unspecified_target".to_string());
    let risk_level =
        string_value(&risk_output, "risk_level", "medium").unwrap_or_else(|| "medium".to_string());
    let approval_output = approvals::approval_requirement_command(&json!({
        "capability": capability,
        "decision": risk_decision,
        "risk_level": risk_level,
        "action_class": action_class,
        "target_summary": target_summary,
    }));
    let approval_required = approval_output
        .get("approval_required")
        .and_then(Value::as_bool)
        .unwrap_or(false)
        || context_gate.approval_reason.is_some()
        || policy_decision.kind == PolicyDecisionKind::RequireApproval;
    let final_decision = if approval_required {
        "require_approval"
    } else {
        risk_decision.as_str()
    };
    let final_reason = if approval_required {
        context_gate.approval_reason.clone().unwrap_or_else(|| {
            if policy_decision.kind == PolicyDecisionKind::RequireApproval {
                policy_decision.reason.clone()
            } else {
                "approval_required".to_string()
            }
        })
    } else {
        "authorization_allowed".to_string()
    };

    json!({
        "ok": final_decision != "block",
        "decision": final_decision,
        "reason": final_reason,
        "next_action": if approval_required {
            "request_tool_execution_approval"
        } else {
            "allow_tool_execution"
        },
        "capability": capability,
        "requested_domain": requested_domain,
        "requested_path": requested_path,
        "safe_mode": safe_mode_output,
        "policy_decision": {
            "decision": policy_decision.decision_name(),
            "reason": policy_decision.reason,
        },
        "risk": risk_output,
        "approval_required": approval_required,
        "approval": approval_output.get("approval").cloned().unwrap_or(Value::Null),
        "cacheable": approval_output
            .get("cacheable")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        "audit_visibility": approval_output
            .get("audit_visibility")
            .and_then(Value::as_str)
            .unwrap_or("standard"),
    })
}

#[derive(Default)]
struct PolicyContextGate {
    block_reason: Option<String>,
    approval_reason: Option<String>,
}

fn evaluate_policy_context(input: &Value) -> PolicyContextGate {
    let payload = input.get("payload").unwrap_or(&Value::Null);
    let policy_context = payload.get("policy_context").unwrap_or(&Value::Null);
    let runtime_policy_context = payload
        .get("runtime_policy_context")
        .unwrap_or(&Value::Null);
    let requested_capability_ids =
        protocol::string_vec_field(policy_context, "requested_capability_ids");
    let workspace_denied_capabilities =
        protocol::string_vec_field(policy_context, "workspace_denied_capabilities");
    let workspace_allowed_capabilities =
        protocol::string_vec_field(policy_context, "workspace_allowed_capabilities");
    let workspace_role =
        protocol::string_field(policy_context, "workspace_role").unwrap_or_default();

    if bool_value(policy_context, "disabled_capability") {
        return PolicyContextGate {
            block_reason: Some("capability_disabled_by_policy_scope".to_string()),
            approval_reason: None,
        };
    }
    if workspace_denied_capabilities.iter().any(|item| item == "*")
        || requested_capability_ids.iter().any(|capability| {
            workspace_denied_capabilities
                .iter()
                .any(|entry| entry == capability)
        })
    {
        return PolicyContextGate {
            block_reason: Some("workspace_capability_denied".to_string()),
            approval_reason: None,
        };
    }
    if !workspace_allowed_capabilities.is_empty()
        && !workspace_allowed_capabilities
            .iter()
            .any(|item| item == "*")
        && workspace_role != "owner"
        && requested_capability_ids.iter().any(|capability| {
            !workspace_allowed_capabilities
                .iter()
                .any(|entry| entry == capability)
        })
    {
        return PolicyContextGate {
            block_reason: Some("workspace_capability_not_granted".to_string()),
            approval_reason: None,
        };
    }
    if bool_value(policy_context, "tool_disabled") {
        return PolicyContextGate {
            block_reason: Some("tool_disabled".to_string()),
            approval_reason: None,
        };
    }
    if protocol::string_field(policy_context, "unsupported_capability").is_some() {
        return PolicyContextGate {
            block_reason: Some("blocked_unsupported_capability".to_string()),
            approval_reason: None,
        };
    }
    if bool_value(policy_context, "blocked_raw_shell_command") {
        return PolicyContextGate {
            block_reason: Some("blocked_raw_shell_command".to_string()),
            approval_reason: None,
        };
    }
    if bool_value(policy_context, "action_blocked")
        && !bool_value(policy_context, "uses_capability_path")
        && !bool_value(policy_context, "safe_raw_shell_command")
    {
        return PolicyContextGate {
            block_reason: Some("blocked_by_action_policy".to_string()),
            approval_reason: None,
        };
    }
    if bool_value(policy_context, "cloud_target")
        && bool_value(policy_context, "critical")
        && bool_value(policy_context, "block_cloud_critical")
    {
        return PolicyContextGate {
            block_reason: Some("blocked_cloud_critical".to_string()),
            approval_reason: None,
        };
    }

    let runtime_execution_decision =
        protocol::string_field(runtime_policy_context, "execution_decision").unwrap_or_default();
    if runtime_execution_decision == "deny" {
        return PolicyContextGate {
            block_reason: Some(
                protocol::string_field(runtime_policy_context, "reason")
                    .filter(|reason| !reason.is_empty())
                    .unwrap_or_else(|| "runtime_policy_blocked".to_string()),
            ),
            approval_reason: None,
        };
    }

    let approval_reason = if bool_value(policy_context, "action_approval_required")
        && !bool_value(policy_context, "safe_raw_shell_command")
    {
        Some("approval_required_by_action_policy".to_string())
    } else if runtime_execution_decision == "require_confirmation" {
        Some(
            protocol::string_field(runtime_policy_context, "reason")
                .filter(|reason| !reason.is_empty())
                .unwrap_or_else(|| "runtime_policy_requires_approval".to_string()),
        )
    } else {
        None
    };

    PolicyContextGate {
        block_reason: None,
        approval_reason,
    }
}

fn authorization_block(
    reason: &str,
    capability: Option<String>,
    requested_domain: Option<String>,
    requested_path: Option<String>,
) -> Value {
    json!({
        "ok": false,
        "decision": "block",
        "reason": reason,
        "next_action": "deny_tool_execution",
        "capability": capability,
        "requested_domain": requested_domain,
        "requested_path": requested_path,
        "safe_mode": null,
        "policy_decision": {
            "decision": "block",
            "reason": reason,
        },
        "risk": null,
        "approval_required": false,
        "approval": null,
        "cacheable": false,
        "audit_visibility": "security",
    })
}

fn string_value(input: &Value, key: &str, fallback: &str) -> Option<String> {
    input
        .get(key)
        .and_then(Value::as_str)
        .map(ToOwned::to_owned)
        .or_else(|| {
            if fallback.is_empty() {
                None
            } else {
                Some(fallback.to_string())
            }
        })
}

fn bool_value(input: &Value, key: &str) -> bool {
    input.get(key).and_then(Value::as_bool).unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_policy_blocks_authorization() {
        let response = authorize_request_command(&json!({"capability": "read_files"}));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "policy_missing");
        assert_eq!(response["next_action"], "deny_tool_execution");
    }

    #[test]
    fn yolo_read_authorization_allows() {
        let response = authorize_request_command(&json!({
            "policy": {"policy_id": "p1", "autonomy_mode": "yolo"},
            "capability": "read_files",
            "action_class": "read"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["approval_required"], false);
        assert_eq!(response["next_action"], "allow_tool_execution");
    }

    #[test]
    fn cautious_shell_authorization_requires_approval() {
        let response = authorize_request_command(&json!({
            "policy": {"policy_id": "p1", "autonomy_mode": "cautious"},
            "capability": "shell",
            "action_class": "execute",
            "target_summary": "cargo test"
        }));
        assert_eq!(response["decision"], "require_approval");
        assert_eq!(response["approval_required"], true);
        assert_eq!(response["approval"]["single_use"], true);
        assert_eq!(response["next_action"], "request_tool_execution_approval");
    }

    #[test]
    fn authorization_blocks_policy_scope_violation() {
        let response = authorize_request_command(&json!({
            "policy": {
                "policy_id": "p1",
                "autonomy_mode": "yolo",
                "domain_allowlist": ["example.com"]
            },
            "capability": "network_access",
            "requested_domain": "evil.test"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "domain_outside_policy_scope");
        assert_eq!(response["risk"], Value::Null);
        assert_eq!(response["next_action"], "deny_tool_execution");
    }

    #[test]
    fn authorization_blocks_kill_switch_before_policy_risk() {
        let response = authorize_request_command(&json!({
            "policy": {"policy_id": "p1", "autonomy_mode": "yolo"},
            "capability": "read_files",
            "kill_switch_enabled": true
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "kill_switch_enabled");
        assert_eq!(response["safe_mode"]["decision"], "block");
        assert_eq!(response["risk"], Value::Null);
        assert_eq!(response["next_action"], "deny_tool_execution");
    }

    #[test]
    fn authorization_allows_with_degraded_safe_mode_context() {
        let response = authorize_request_command(&json!({
            "policy": {"policy_id": "p1", "autonomy_mode": "yolo"},
            "capability": "read_files",
            "incident_mode": "drain",
            "action_class": "read"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["safe_mode"]["decision"], "degrade");
        assert_eq!(response["safe_mode"]["runtime_mode"], "safe");
        assert_eq!(response["next_action"], "allow_tool_execution");
    }

    #[test]
    fn authorization_blocks_workspace_capability_denial_from_policy_context() {
        let response = authorize_request_command(&json!({
            "policy": {"policy_id": "p1", "autonomy_mode": "yolo"},
            "capability": "filesystem_read_write",
            "payload": {
                "policy_context": {
                    "requested_capability_ids": ["filesystem.read_write"],
                    "workspace_denied_capabilities": ["filesystem.read_write"]
                }
            }
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "workspace_capability_denied");
        assert_eq!(response["next_action"], "deny_tool_execution");
    }

    #[test]
    fn authorization_requires_approval_from_runtime_policy_context() {
        let response = authorize_request_command(&json!({
            "policy": {"policy_id": "p1", "autonomy_mode": "yolo"},
            "capability": "filesystem_read_write",
            "payload": {
                "runtime_policy_context": {
                    "execution_decision": "require_confirmation",
                    "reason": "runtime_policy_requires_review"
                }
            },
            "action_class": "write"
        }));
        assert_eq!(response["decision"], "require_approval");
        assert_eq!(response["reason"], "runtime_policy_requires_review");
        assert_eq!(response["approval_required"], true);
        assert_eq!(response["next_action"], "request_tool_execution_approval");
    }
}

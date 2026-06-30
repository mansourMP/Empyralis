use serde_json::{json, Value};

const GATEWAY_BOUND_OPERATIONS: &[&str] = &[
    "websocket_connect",
    "protocol_route",
    "tool_execute",
    "tool_interrupt",
    "browser_session",
    "browser_action",
    "approval_request",
    "approval_resolve",
    "approval_memory_consume",
    "quota_check",
    "diagnostics_export",
    "browser_fallback",
    "cloud_fallback",
    "gateway_policy_read",
    "gateway_policy_write",
];

const METERED_OPERATIONS: &[&str] = &[
    "tool_execute",
    "browser_session",
    "browser_action",
    "browser_fallback",
    "cloud_fallback",
];

const TOOL_OR_BROWSER_OPERATIONS: &[&str] = &[
    "protocol_route",
    "tool_execute",
    "tool_interrupt",
    "browser_session",
    "browser_action",
    "browser_fallback",
    "cloud_fallback",
];

const PRIVILEGED_OPERATIONS: &[&str] = &[
    "tool_execute",
    "tool_interrupt",
    "browser_action",
    "approval_request",
    "approval_resolve",
    "approval_memory_consume",
    "diagnostics_export",
    "gateway_policy_write",
    "cloud_fallback",
];

pub fn gateway_service_decision_command(payload: &Value) -> Result<Value, String> {
    let operation = text(payload, "operation", "unknown");
    let tenant_id = payload
        .get("tenant_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let workspace_id = payload
        .get("workspace_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let actor_id = payload
        .get("actor_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let actor_role = text(payload, "actor_role", "member");
    let gateway_id = payload
        .get("gateway_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let session_id = payload
        .get("session_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let request_id = payload
        .get("request_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let capability_id = payload
        .get("capability_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let run_id = payload.get("run_id").and_then(Value::as_str).unwrap_or("");
    let trace_id = payload
        .get("trace_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let quota_profile = text(payload, "quota_profile", "standard");
    let risk_level = first_text(payload, &["risk_level", "risk_decision"], "normal");
    let policy_decision = text(payload, "policy_decision", "allow");
    let device_trust_state = text(payload, "device_trust_state", "trusted");
    let protocol_version = payload
        .get("protocol_version")
        .and_then(Value::as_str)
        .unwrap_or("");
    let browser_session_id = payload
        .get("browser_session_id")
        .and_then(Value::as_str)
        .unwrap_or("");

    let approval_provided = flag(payload, "approval_provided", false);
    let approval_memory_hit = flag(payload, "approval_memory_hit", false);
    let kill_switch_enabled = flag(payload, "kill_switch_enabled", false);
    let quota_ok = flag(payload, "quota_ok", true);
    let gateway_registered = flag(payload, "gateway_registered", true);
    let session_valid = flag(payload, "session_valid", true);
    let websocket_token_present = flag(payload, "websocket_token_present", true);
    let frame_valid = flag(payload, "frame_valid", true);
    let payload_present = flag(payload, "payload_present", true);
    let diagnostics_export_approval = flag(payload, "diagnostics_export_approval", false);
    let safe_mode_enabled = flag(payload, "safe_mode_enabled", false);
    let cloud_fallback_enabled = flag(payload, "cloud_fallback_enabled", false);
    let cloud_fallback_approved = flag(payload, "cloud_fallback_approved", false);

    let mut block_reasons: Vec<String> = Vec::new();
    let mut approval_reasons: Vec<String> = Vec::new();

    let requires_workspace = operation != "health_check";
    let requires_gateway_identity = contains(GATEWAY_BOUND_OPERATIONS, &operation);
    let metered_operation = contains(METERED_OPERATIONS, &operation);
    let privileged_operation = contains(PRIVILEGED_OPERATIONS, &operation);
    let tool_or_browser_operation = contains(TOOL_OR_BROWSER_OPERATIONS, &operation);

    if requires_workspace && tenant_id.trim().is_empty() {
        block_reasons.push("missing_tenant".to_string());
    }
    if requires_workspace && workspace_id.trim().is_empty() {
        block_reasons.push("missing_workspace".to_string());
    }
    if requires_workspace && actor_id.trim().is_empty() {
        block_reasons.push("missing_actor".to_string());
    }
    if requires_gateway_identity && gateway_id.trim().is_empty() {
        block_reasons.push("missing_gateway_id".to_string());
    }
    if requires_gateway_identity && session_id.trim().is_empty() {
        block_reasons.push("missing_session_id".to_string());
    }
    if requires_gateway_identity && !gateway_registered {
        block_reasons.push("gateway_not_registered".to_string());
    }
    if requires_gateway_identity && !session_valid {
        block_reasons.push("invalid_gateway_session".to_string());
    }
    if operation == "websocket_connect" && !websocket_token_present {
        block_reasons.push("missing_websocket_token".to_string());
    }
    if (operation == "protocol_route" || tool_or_browser_operation)
        && protocol_version.trim().is_empty()
    {
        block_reasons.push("missing_protocol_version".to_string());
    }
    if (operation == "protocol_route" || tool_or_browser_operation) && !frame_valid {
        block_reasons.push("invalid_gateway_frame".to_string());
    }
    if (operation == "protocol_route" || tool_or_browser_operation) && !payload_present {
        block_reasons.push("missing_gateway_payload".to_string());
    }
    if tool_or_browser_operation && kill_switch_enabled {
        block_reasons.push("gateway_kill_switch_enabled".to_string());
    }
    if metered_operation && !quota_ok {
        block_reasons.push("quota_exceeded".to_string());
    }
    if privileged_operation && is_untrusted_device(&device_trust_state) {
        block_reasons.push("untrusted_gateway_device".to_string());
    }
    if policy_decision == "block" || policy_decision == "deny" || policy_decision == "rejected" {
        block_reasons.push("policy_denied".to_string());
    }
    if risk_level == "block" || risk_level == "deny" || risk_level == "critical_block" {
        block_reasons.push("risk_denied".to_string());
    }
    if (operation == "approval_request"
        || operation == "approval_resolve"
        || operation == "tool_execute")
        && run_id.trim().is_empty()
    {
        block_reasons.push("missing_run_id".to_string());
    }
    if operation == "browser_action" && browser_session_id.trim().is_empty() {
        block_reasons.push("missing_browser_session".to_string());
    }
    if operation == "diagnostics_export" && !diagnostics_export_approval {
        block_reasons.push("diagnostics_export_not_approved".to_string());
    }
    if operation == "gateway_policy_write" && actor_role != "owner" && actor_role != "admin" {
        block_reasons.push("policy_write_requires_owner_or_admin".to_string());
    }
    if operation == "cloud_fallback" && !cloud_fallback_enabled {
        block_reasons.push("cloud_fallback_disabled".to_string());
    }
    if operation == "cloud_fallback" && !cloud_fallback_approved {
        block_reasons.push("cloud_fallback_not_approved".to_string());
    }

    if requires_approval_for_risk(&risk_level) {
        approval_reasons.push("risk_requires_approval".to_string());
    }
    if safe_mode_enabled && privileged_operation {
        approval_reasons.push("safe_mode_privileged_gateway_operation".to_string());
    }
    if privileged_operation && operation != "approval_request" && !approval_memory_hit {
        approval_reasons.push("approval_memory_miss".to_string());
    }
    if operation == "browser_action" {
        approval_reasons.push("browser_action_requires_owner_approval".to_string());
    }
    if operation == "cloud_fallback" {
        approval_reasons.push("cloud_fallback_requires_explicit_approval".to_string());
    }
    if operation == "diagnostics_export" {
        approval_reasons.push("diagnostics_export_requires_audit_approval".to_string());
    }

    let approval_required = !approval_provided && !approval_reasons.is_empty();
    let blocked = !block_reasons.is_empty();
    let decision = if blocked {
        "block"
    } else if approval_required {
        "requires_approval"
    } else {
        "allow"
    };
    let next_action = if blocked {
        "do_not_dispatch_gateway_operation"
    } else if operation == "approval_request" {
        "request_gateway_owner_approval"
    } else if approval_required {
        "request_gateway_owner_approval"
    } else if operation == "health_check" {
        "publish_gateway_health"
    } else if operation == "approval_resolve" {
        "persist_approval_decision"
    } else if tool_or_browser_operation {
        "dispatch_gateway_operation"
    } else {
        "allow_gateway_service_operation"
    };
    let audit_visibility =
        if operation == "diagnostics_export" || operation == "gateway_policy_write" {
            "security_audit"
        } else if privileged_operation || approval_required {
            "admin_audit"
        } else {
            "standard_audit"
        };
    let reason = if blocked {
        block_reasons.join(",")
    } else if approval_required {
        approval_reasons.join(",")
    } else {
        "gateway_service_operation_allowed".to_string()
    };

    Ok(json!({
        "ok": !blocked,
        "decision": decision,
        "reason": reason,
        "operation": operation,
        "next_action": next_action,
        "approval_required": approval_required,
        "audit_visibility": audit_visibility,
        "scope": {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "actor_id": actor_id,
            "actor_role": actor_role,
            "run_id": run_id,
            "trace_id": trace_id,
        },
        "gateway": {
            "gateway_id": gateway_id,
            "session_id": session_id,
            "request_id": request_id,
            "capability_id": capability_id,
            "protocol_version": protocol_version,
            "gateway_registered": gateway_registered,
            "session_valid": session_valid,
            "device_trust_state": device_trust_state,
        },
        "risk": {
            "risk_level": risk_level,
            "policy_decision": policy_decision,
            "safe_mode_enabled": safe_mode_enabled,
        },
        "quota": {
            "profile": quota_profile,
            "metered_operation": metered_operation,
            "quota_ok": quota_ok,
        },
        "approval": {
            "provided": approval_provided,
            "memory_hit": approval_memory_hit,
            "reasons": approval_reasons,
        },
        "block_reasons": block_reasons,
        "normalized_flags": {
            "requires_workspace": requires_workspace,
            "requires_gateway_identity": requires_gateway_identity,
            "privileged_operation": privileged_operation,
            "tool_or_browser_operation": tool_or_browser_operation,
            "kill_switch_enabled": kill_switch_enabled,
            "cloud_fallback_enabled": cloud_fallback_enabled,
            "cloud_fallback_approved": cloud_fallback_approved,
        }
    }))
}

fn contains(values: &[&str], needle: &str) -> bool {
    values.iter().any(|value| *value == needle)
}

fn first_text(payload: &Value, keys: &[&str], default: &str) -> String {
    for key in keys {
        if let Some(value) = payload.get(*key).and_then(Value::as_str) {
            return normalize(value);
        }
    }
    normalize(default)
}

fn text(payload: &Value, key: &str, default: &str) -> String {
    payload
        .get(key)
        .and_then(Value::as_str)
        .map(normalize)
        .unwrap_or_else(|| normalize(default))
}

fn flag(payload: &Value, key: &str, default: bool) -> bool {
    match payload.get(key) {
        Some(Value::Bool(value)) => *value,
        Some(Value::String(value)) => match normalize(value).as_str() {
            "1" | "true" | "yes" | "y" | "enabled" | "allow" | "allowed" | "ok" => true,
            "0" | "false" | "no" | "n" | "disabled" | "deny" | "denied" | "block" => false,
            _ => default,
        },
        Some(Value::Number(value)) => value.as_i64().unwrap_or(0) != 0,
        _ => default,
    }
}

fn normalize(value: &str) -> String {
    value
        .trim()
        .to_ascii_lowercase()
        .replace('-', "_")
        .replace(' ', "_")
}

fn is_untrusted_device(device_trust_state: &str) -> bool {
    matches!(
        device_trust_state,
        "blocked" | "denied" | "untrusted" | "unknown" | "revoked"
    )
}

fn requires_approval_for_risk(risk_level: &str) -> bool {
    matches!(
        risk_level,
        "approval" | "requires_approval" | "high" | "critical" | "privileged"
    )
}

#[cfg(test)]
mod gateway_service_kernel_boundary_tests {
    use super::gateway_service_decision_command;

    #[test]
    fn tool_execute_blocks_when_kill_switch_enabled() {
        let decision = gateway_service_decision_command(&serde_json::json!({
            "operation": "tool_execute",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "system",
            "actor_role": "system",
            "gateway_id": "gw-1",
            "session_id": "session-1",
            "run_id": "run-1",
            "capability_id": "computer_control.click",
            "protocol_version": "gateway.v1",
            "kill_switch_enabled": true,
            "approval_memory_hit": true
        }))
        .unwrap();

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("block")
        );
        assert!(decision
            .get("reason")
            .and_then(|value| value.as_str())
            .unwrap_or("")
            .contains("gateway_kill_switch_enabled"));
    }

    #[test]
    fn tool_execute_allows_satisfied_gateway_policy() {
        let decision = gateway_service_decision_command(&serde_json::json!({
            "operation": "tool_execute",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "system",
            "actor_role": "system",
            "gateway_id": "gw-1",
            "session_id": "session-1",
            "run_id": "run-1",
            "capability_id": "computer_control.click",
            "protocol_version": "gateway.v1",
            "gateway_registered": true,
            "session_valid": true,
            "quota_ok": true,
            "approval_memory_hit": true
        }))
        .unwrap();

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("allow")
        );
        assert_eq!(
            decision.get("next_action").and_then(|value| value.as_str()),
            Some("dispatch_gateway_operation")
        );
    }
}

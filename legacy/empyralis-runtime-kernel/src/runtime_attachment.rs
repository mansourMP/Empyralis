use serde_json::{json, Value};

const SUPPORTED_ATTACHMENT_KINDS: &[&str] = &[
    "managed_cloud",
    "cloud_computer",
    "local_companion",
    "self_hosted_business_node",
];

const SUPPORTED_RUNTIME_MODES: &[&str] = &["hosted_secure", "local_secure", "privileged_device"];
const SUPPORTED_RUNTIME_TARGETS: &[&str] = &[
    "cloud_default",
    "sage_cloud_computer",
    "local_companion",
    "self_host_runtime",
];

pub fn runtime_attachment_decision_command(payload: &Value) -> Value {
    let operation = normalize_operation(&text_any(payload, &["operation", "action"]));
    if operation.is_empty() {
        return block("runtime_attachment_operation_missing", "unknown");
    }
    if !known_operation(&operation) {
        return block("runtime_attachment_operation_unknown", &operation);
    }

    let tenant_id = text_any(payload, &["tenant_id", "owner_tenant_id"]);
    if tenant_id.is_empty() {
        return block("tenant_id_missing", &operation);
    }
    let workspace_id = text_any(payload, &["workspace_id", "owner_workspace_id"]);
    if workspace_id.is_empty() {
        return block("workspace_id_missing", &operation);
    }
    if bool_field(payload, "workspace_disabled") {
        return block("runtime_attachment_workspace_disabled", &operation);
    }

    match operation.as_str() {
        "normalize_target" => normalize_target_decision(payload, &tenant_id, &workspace_id),
        "build_targets" => build_targets_decision(payload, &tenant_id, &workspace_id),
        "select_attachment" => select_attachment_decision(payload, &tenant_id, &workspace_id),
        "self_hosted_gate" => self_hosted_gate_decision(payload, &tenant_id, &workspace_id),
        "local_companion_gate" => local_companion_gate_decision(payload, &tenant_id, &workspace_id),
        "usage_credit_event" => usage_credit_event_decision(payload, &tenant_id, &workspace_id),
        _ => block("runtime_attachment_operation_unknown", &operation),
    }
}

fn normalize_target_decision(payload: &Value, tenant_id: &str, workspace_id: &str) -> Value {
    let target = normalize_runtime_target(&text_any(
        payload,
        &["runtime_target", "target", "execution_target"],
    ));
    if target.is_empty() {
        return block("runtime_target_missing", "normalize_target");
    }
    if !SUPPORTED_RUNTIME_TARGETS.contains(&target.as_str()) {
        return block("runtime_target_unsupported", "normalize_target");
    }
    allow(
        "runtime_target_normalized",
        "normalize_target",
        "normalize_runtime_target_id",
        tenant_id,
        workspace_id,
        "",
        "",
        true,
        "standard",
    )
    .with_extra("runtime_target", json!(target))
    .with_extra(
        "canonical_runtime_target",
        json!(canonical_runtime_target(&target)),
    )
}

fn build_targets_decision(payload: &Value, tenant_id: &str, workspace_id: &str) -> Value {
    let attachment_count =
        numeric_any(payload, &["attachment_count", "attachments_count"]).unwrap_or(0);
    let deployment_mode = normalize_deployment_mode(&text_any(payload, &["deployment_mode"]));
    allow(
        "runtime_targets_build_allowed",
        "build_targets",
        "build_workspace_runtime_targets",
        tenant_id,
        workspace_id,
        "",
        "",
        true,
        "standard",
    )
    .with_extra("attachment_count", json!(attachment_count))
    .with_extra("deployment_mode", json!(deployment_mode))
    .with_extra(
        "default_runtime_target",
        json!(default_runtime_target(&deployment_mode, attachment_count)),
    )
}

fn select_attachment_decision(payload: &Value, tenant_id: &str, workspace_id: &str) -> Value {
    let attachment_kind =
        normalize_attachment_kind(&text_any(payload, &["attachment_kind", "kind"]));
    if attachment_kind.is_empty() {
        return block("attachment_kind_missing", "select_attachment");
    }
    if !SUPPORTED_ATTACHMENT_KINDS.contains(&attachment_kind.as_str()) {
        return block("attachment_kind_unsupported", "select_attachment");
    }
    if !attachment_available(payload, &attachment_kind) {
        return block("runtime_attachment_unavailable", "select_attachment");
    }
    if !boolish(payload, "capability_match", true) {
        return block(
            "runtime_attachment_capability_mismatch",
            "select_attachment",
        );
    }
    if !boolish(payload, "connector_match", true) {
        return block("runtime_attachment_connector_mismatch", "select_attachment");
    }
    if !text_any(payload, &["requested_machine_target", "machine_target"]).is_empty()
        && !boolish(payload, "machine_target_match", false)
    {
        return block(
            "runtime_attachment_machine_target_mismatch",
            "select_attachment",
        );
    }
    allow(
        "runtime_attachment_selected",
        "select_attachment",
        "select_runtime_attachment",
        tenant_id,
        workspace_id,
        &text_any(payload, &["attachment_id", "runtime_id", "machine_id"]),
        &attachment_kind,
        false,
        "security",
    )
}

fn self_hosted_gate_decision(payload: &Value, tenant_id: &str, workspace_id: &str) -> Value {
    if text_any(payload, &["runtime_profile_id"]).is_empty() {
        return block("self_hosted_runtime_profile_required", "self_hosted_gate");
    }
    let attachment_kind =
        normalize_attachment_kind(&text_any(payload, &["attachment_kind", "kind"]));
    if attachment_kind != "self_hosted_business_node" {
        return block("self_hosted_node_required", "self_hosted_gate");
    }
    if !attachment_available(payload, &attachment_kind) {
        return block("self_hosted_node_unavailable", "self_hosted_gate");
    }
    if !boolish(payload, "capability_match", true) {
        return block("self_hosted_node_capability_mismatch", "self_hosted_gate");
    }
    if bool_field(payload, "allowed_agent_ids_present") && !bool_field(payload, "agent_allowed") {
        return block("self_hosted_agent_not_allowed", "self_hosted_gate");
    }
    let max_concurrent_sessions = numeric_any(payload, &["max_concurrent_sessions"]).unwrap_or(0);
    let active_sessions =
        numeric_any(payload, &["active_sessions", "active_session_count"]).unwrap_or(0);
    if max_concurrent_sessions > 0
        && active_sessions >= max_concurrent_sessions
        && !bool_field(payload, "existing_session_reuse")
    {
        return block("self_hosted_node_concurrency_exceeded", "self_hosted_gate");
    }
    allow(
        "self_hosted_node_gate_allowed",
        "self_hosted_gate",
        "ensure_self_hosted_node_gate",
        tenant_id,
        workspace_id,
        &text_any(payload, &["attachment_id", "runtime_node_id", "machine_id"]),
        &attachment_kind,
        false,
        "security",
    )
    .with_extra("max_concurrent_sessions", json!(max_concurrent_sessions))
    .with_extra("active_sessions", json!(active_sessions))
}

fn local_companion_gate_decision(payload: &Value, tenant_id: &str, workspace_id: &str) -> Value {
    let attachment_kind =
        normalize_attachment_kind(&text_any(payload, &["attachment_kind", "kind"]));
    if attachment_kind != "local_companion" {
        return block("local_companion_required", "local_companion_gate");
    }
    if !attachment_available(payload, &attachment_kind) {
        return block("local_companion_unavailable", "local_companion_gate");
    }
    if !boolish(payload, "capability_readiness_match", true) {
        return block(
            "local_companion_capability_not_ready",
            "local_companion_gate",
        );
    }
    allow(
        "local_companion_gate_allowed",
        "local_companion_gate",
        "select_local_companion_attachment",
        tenant_id,
        workspace_id,
        &text_any(payload, &["attachment_id", "gateway_id", "device_id"]),
        &attachment_kind,
        false,
        "security",
    )
}

fn usage_credit_event_decision(payload: &Value, tenant_id: &str, workspace_id: &str) -> Value {
    let surface = normalize_token(&text_any(payload, &["surface"]));
    if !matches!(surface.as_str(), "sage" | "studio" | "mini_app") {
        return block("runtime_usage_surface_invalid", "usage_credit_event");
    }
    let target = normalize_runtime_target(&text_any(payload, &["runtime_target", "target"]));
    if !SUPPORTED_RUNTIME_TARGETS.contains(&target.as_str()) {
        return block("runtime_target_unsupported", "usage_credit_event");
    }
    if text_any(payload, &["session_id"]).is_empty() {
        return block("runtime_usage_session_id_missing", "usage_credit_event");
    }
    let active_seconds = numeric_any(payload, &["active_seconds"]).unwrap_or(0);
    let billable_seconds = numeric_any(payload, &["billable_seconds"]).unwrap_or(0);
    if billable_seconds > active_seconds {
        return block(
            "runtime_usage_billable_exceeds_active",
            "usage_credit_event",
        );
    }
    allow(
        "runtime_usage_credit_event_allowed",
        "usage_credit_event",
        "build_runtime_usage_credit_event",
        tenant_id,
        workspace_id,
        &text_any(payload, &["session_id"]),
        &target,
        false,
        "standard",
    )
    .with_extra("surface", json!(surface))
    .with_extra("active_seconds", json!(active_seconds))
    .with_extra("billable_seconds", json!(billable_seconds))
}

fn allow(
    reason: &str,
    operation: &str,
    next_action: &str,
    tenant_id: &str,
    workspace_id: &str,
    attachment_id: &str,
    attachment_kind: &str,
    cacheable: bool,
    audit_visibility: &str,
) -> Value {
    json!({
        "ok": true,
        "decision": "allow",
        "reason": reason,
        "operation": operation,
        "next_action": next_action,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "attachment_id": attachment_id,
        "attachment_kind": attachment_kind,
        "approval_required": false,
        "cacheable": cacheable,
        "audit_visibility": audit_visibility,
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
        "target" | "normalize_runtime_target" => "normalize_target".to_string(),
        "targets" | "workspace_targets" => "build_targets".to_string(),
        "select" | "select_runtime_attachment" => "select_attachment".to_string(),
        "self_hosted" | "ensure_self_hosted_node_gate" => "self_hosted_gate".to_string(),
        "local" | "local_gateway" | "local_companion" => "local_companion_gate".to_string(),
        "usage" | "credit_event" => "usage_credit_event".to_string(),
        other => other.to_string(),
    }
}

fn known_operation(operation: &str) -> bool {
    matches!(
        operation,
        "normalize_target"
            | "build_targets"
            | "select_attachment"
            | "self_hosted_gate"
            | "local_companion_gate"
            | "usage_credit_event"
    )
}

fn normalize_runtime_target(raw: &str) -> String {
    match normalize_token(raw).as_str() {
        "user_device" | "gateway" | "empyralis_gateway" => "local_companion".to_string(),
        "cloud_computer" | "cloud_desktop" => "sage_cloud_computer".to_string(),
        "self_hosted_runtime" | "self_hosted_business_node" => "self_host_runtime".to_string(),
        other => other.to_string(),
    }
}

fn canonical_runtime_target(target: &str) -> &'static str {
    match target {
        "local_companion" => "user_device_gateway",
        "sage_cloud_computer" => "empyralis_cloud_computer",
        "self_host_runtime" => "self_hosted_node",
        _ => "cloud_default",
    }
}

fn normalize_attachment_kind(raw: &str) -> String {
    match normalize_token(raw).as_str() {
        "gateway" | "user_device_gateway" | "desktop_companion" => "local_companion".to_string(),
        "self_hosted_node" | "self_host_runtime" | "enterprise_node" => {
            "self_hosted_business_node".to_string()
        }
        "cloud_desktop" | "hosted_cloud_computer" => "cloud_computer".to_string(),
        other => other.to_string(),
    }
}

fn normalize_deployment_mode(raw: &str) -> String {
    match normalize_token(raw).as_str() {
        "local" => "local_only".to_string(),
        "self_hosted" => "self_hosted_business".to_string(),
        "cloud" | "" => "cloud_only".to_string(),
        other => other.to_string(),
    }
}

fn default_runtime_target(deployment_mode: &str, attachment_count: u64) -> &'static str {
    match deployment_mode {
        "local_only" => "local_companion",
        "self_hosted_business" => "self_host_runtime",
        "hybrid" if attachment_count > 0 => "local_companion",
        _ => "cloud_default",
    }
}

fn attachment_available(payload: &Value, attachment_kind: &str) -> bool {
    if attachment_kind == "managed_cloud" {
        return true;
    }
    let status = normalize_token(&text_any(payload, &["status", "attachment_status"]));
    let control_state = normalize_token(&text_any(payload, &["control_state"]));
    bool_field(payload, "online")
        && bool_field(payload, "healthy")
        && !matches!(
            status.as_str(),
            "offline" | "failed" | "stopped" | "revoked" | "blocked"
        )
        && !matches!(control_state.as_str(), "revoked" | "blocked" | "disabled")
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

fn boolish(payload: &Value, key: &str, default: bool) -> bool {
    match payload.get(key) {
        Some(Value::Bool(value)) => *value,
        Some(Value::String(value)) => match value.trim().to_ascii_lowercase().as_str() {
            "1" | "true" | "yes" | "y" | "enabled" => true,
            "0" | "false" | "no" | "n" | "disabled" => false,
            _ => default,
        },
        _ => default,
    }
}

fn normalize_token(raw: &str) -> String {
    raw.trim()
        .to_ascii_lowercase()
        .replace('-', "_")
        .replace(' ', "_")
}

#[cfg(test)]
mod runtime_attachment_extension_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn normalize_target_maps_public_cloud_computer_alias() {
        let decision = runtime_attachment_decision_command(&json!({
            "operation": "normalize_target",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "runtime_target": "cloud_computer"
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["runtime_target"], "sage_cloud_computer");
        assert_eq!(
            decision["canonical_runtime_target"],
            "empyralis_cloud_computer"
        );
    }

    #[test]
    fn normalize_target_blocks_unknown_runtime_target() {
        let decision = runtime_attachment_decision_command(&json!({
            "operation": "normalize_target",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "runtime_target": "unknown_runtime"
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "runtime_target_unsupported");
    }
}

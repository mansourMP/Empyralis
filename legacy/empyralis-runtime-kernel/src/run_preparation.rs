use serde_json::{json, Value};

const SUPPORTED_ENGINES: &[&str] = &["orion", "sage", "legacy", "workflow"];
const SUPPORTED_OUTCOME_PACKS: &[&str] = &[
    "",
    "default",
    "local_execution",
    "browser_execution",
    "research",
    "delegation",
    "workflow",
];
const VALID_TRUST_MODES: &[&str] = &["auto", "guarded", "strict", "cost_guard", "sensitive_guard"];
const VALID_EXECUTION_TARGETS: &[&str] = &["auto", "cloud", "local_companion"];
const VALID_ELEVATED_MODES: &[&str] = &["off", "request", "approved", "session"];

pub fn run_preparation_decision_command(input: &Value) -> Value {
    let operation = normalize_operation(
        string_field(input, "operation")
            .or_else(|| string_field(input, "action"))
            .or_else(|| string_field(input, "intent"))
            .as_deref()
            .unwrap_or("prepare_run_start"),
    );
    if operation.is_empty() {
        return block("run_preparation_operation_unknown");
    }

    let workspace_id = string_field(input, "workspace_id");
    let tenant_id = string_field(input, "tenant_id");
    let parent_run_id = string_field(input, "parent_run_id");
    let workflow_id = string_field(input, "workflow_id");
    let agent_role = normalize_optional_token(
        string_field(input, "agent_role")
            .or_else(|| string_field(input, "role"))
            .as_deref()
            .unwrap_or(""),
    );
    let engine = normalize_engine(string_field(input, "engine").as_deref().unwrap_or("orion"));
    let outcome_pack =
        normalize_token(string_field(input, "outcome_pack").as_deref().unwrap_or(""));
    let trust_mode = normalize_trust_mode(
        string_field(input, "trust_mode")
            .as_deref()
            .unwrap_or("auto"),
    );
    let execution_target = normalize_execution_target(
        string_field(input, "execution_target")
            .as_deref()
            .unwrap_or("auto"),
    );
    let elevated_mode = normalize_elevated_mode(
        string_field(input, "elevated_mode")
            .or_else(|| string_field(input, "elevated"))
            .as_deref()
            .unwrap_or("off"),
    );

    if workspace_id.is_none() {
        return block("workspace_id_missing");
    }
    if tenant_id.is_none() && operation != "prepare_system_run" {
        return block("tenant_id_missing");
    }
    if !SUPPORTED_ENGINES.contains(&engine.as_str()) {
        return block("run_engine_unsupported");
    }
    if engine == "orion" && boolish(input, "engine_validation_failed", false) {
        return block("orion_runtime_validation_failed");
    }
    if !SUPPORTED_OUTCOME_PACKS.contains(&outcome_pack.as_str()) {
        return block("outcome_pack_unsupported");
    }
    if !VALID_TRUST_MODES.contains(&trust_mode.as_str()) {
        return block("trust_mode_unsupported");
    }
    if !VALID_EXECUTION_TARGETS.contains(&execution_target.as_str()) {
        return block("execution_target_unsupported");
    }
    if !VALID_ELEVATED_MODES.contains(&elevated_mode.as_str()) {
        return block("elevated_mode_unsupported");
    }
    if positive_i64(input, "max_iterations") < 0 {
        return block("max_iterations_invalid");
    }
    if string_field(input, "max_iterations").is_some() && positive_i64(input, "max_iterations") == 0
    {
        return block("max_iterations_invalid");
    }
    if boolish(input, "pack_inputs_present", false)
        && !boolish(input, "pack_inputs_is_object", false)
    {
        return block("metadata_pack_inputs_must_be_object");
    }
    if boolish(input, "outcome_scope_present", false)
        && !boolish(input, "outcome_scope_is_list", false)
    {
        return block("metadata_outcome_scope_must_be_list");
    }
    if boolish(input, "approval_rules_present", false)
        && !boolish(input, "approval_rules_is_object", false)
    {
        return block("metadata_approval_rules_must_be_object");
    }
    if boolish(input, "schedule_present", false) && !boolish(input, "schedule_is_object", false) {
        return block("metadata_schedule_must_be_object");
    }
    if boolish(input, "connector_credential_present", false)
        && !boolish(input, "connector_credential_is_string", false)
    {
        return block("metadata_connector_credential_id_must_be_string");
    }
    if boolish(input, "workflow_required", false) && workflow_id.is_none() {
        return block("workflow_id_missing");
    }
    if workflow_id.is_some() && !boolish(input, "workflow_snapshot_present", false) {
        return block("workflow_snapshot_missing");
    }
    if boolish(input, "workflow_snapshot_present", false)
        && !boolish(input, "workflow_definition_present", true)
    {
        return block("workflow_definition_missing");
    }
    if boolish(input, "app_id_present", false) && !boolish(input, "app_permissions_resolved", false)
    {
        return block("app_permissions_missing");
    }
    if operation == "prepare_delegated_child" {
        if parent_run_id.is_none() {
            return block("parent_run_id_missing");
        }
        if agent_role.is_none() {
            return block("delegated_agent_role_missing");
        }
    }
    if elevated_mode != "off" && !boolish(input, "elevated_approval_present", false) {
        return require_approval(
            "run_preparation_elevated_mode_requires_approval",
            &operation,
            "request_elevated_mode_approval",
            &engine,
            &outcome_pack,
            &trust_mode,
            &execution_target,
            &elevated_mode,
            workspace_id,
            tenant_id,
            parent_run_id,
            workflow_id,
            agent_role,
        );
    }

    allow(
        "run_preparation_allowed",
        &operation,
        "prepare_run_request",
        &engine,
        &outcome_pack,
        &trust_mode,
        &execution_target,
        &elevated_mode,
        workspace_id,
        tenant_id,
        parent_run_id,
        workflow_id,
        agent_role,
        false,
        "security",
    )
}

fn allow(
    reason: &str,
    operation: &str,
    next_action: &str,
    engine: &str,
    outcome_pack: &str,
    trust_mode: &str,
    execution_target: &str,
    elevated_mode: &str,
    workspace_id: Option<String>,
    tenant_id: Option<String>,
    parent_run_id: Option<String>,
    workflow_id: Option<String>,
    agent_role: Option<String>,
    cacheable: bool,
    audit_visibility: &str,
) -> Value {
    response(
        "allow",
        reason,
        operation,
        next_action,
        engine,
        outcome_pack,
        trust_mode,
        execution_target,
        elevated_mode,
        workspace_id,
        tenant_id,
        parent_run_id,
        workflow_id,
        agent_role,
        false,
        cacheable,
        audit_visibility,
    )
}

fn require_approval(
    reason: &str,
    operation: &str,
    next_action: &str,
    engine: &str,
    outcome_pack: &str,
    trust_mode: &str,
    execution_target: &str,
    elevated_mode: &str,
    workspace_id: Option<String>,
    tenant_id: Option<String>,
    parent_run_id: Option<String>,
    workflow_id: Option<String>,
    agent_role: Option<String>,
) -> Value {
    response(
        "require_approval",
        reason,
        operation,
        next_action,
        engine,
        outcome_pack,
        trust_mode,
        execution_target,
        elevated_mode,
        workspace_id,
        tenant_id,
        parent_run_id,
        workflow_id,
        agent_role,
        true,
        false,
        "security",
    )
}

fn block(reason: &str) -> Value {
    json!({
        "ok": false,
        "decision": "block",
        "reason": reason,
        "approval_required": false,
        "cacheable": false,
        "audit_visibility": "security",
    })
}

fn response(
    decision: &str,
    reason: &str,
    operation: &str,
    next_action: &str,
    engine: &str,
    outcome_pack: &str,
    trust_mode: &str,
    execution_target: &str,
    elevated_mode: &str,
    workspace_id: Option<String>,
    tenant_id: Option<String>,
    parent_run_id: Option<String>,
    workflow_id: Option<String>,
    agent_role: Option<String>,
    approval_required: bool,
    cacheable: bool,
    audit_visibility: &str,
) -> Value {
    json!({
        "ok": decision != "block",
        "decision": decision,
        "reason": reason,
        "operation": operation,
        "next_action": next_action,
        "engine": engine,
        "outcome_pack": outcome_pack,
        "trust_mode": trust_mode,
        "execution_target": execution_target,
        "elevated_mode": elevated_mode,
        "workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "parent_run_id": parent_run_id,
        "workflow_id": workflow_id,
        "agent_role": agent_role,
        "approval_required": approval_required || decision == "require_approval",
        "cacheable": cacheable,
        "audit_visibility": audit_visibility,
    })
}

fn normalize_operation(value: &str) -> String {
    match normalize_token(value).as_str() {
        "prepare_run_start" | "run_start" | "start_run" => "prepare_run_start".to_string(),
        "prepare_turn_start" | "turn_start" | "agent_turn" => "prepare_turn_start".to_string(),
        "prepare_legacy_run" | "legacy_run" => "prepare_legacy_run".to_string(),
        "prepare_system_run" | "system_run" | "scheduled_run" => "prepare_system_run".to_string(),
        "prepare_delegated_child" | "delegated_child" | "child_run" => {
            "prepare_delegated_child".to_string()
        }
        _ => String::new(),
    }
}

fn normalize_engine(value: &str) -> String {
    match normalize_token(value).as_str() {
        "" | "orion" | "empyralis" => "orion".to_string(),
        "sage" => "sage".to_string(),
        "legacy" => "legacy".to_string(),
        "workflow" => "workflow".to_string(),
        other => other.to_string(),
    }
}

fn normalize_trust_mode(value: &str) -> String {
    match normalize_token(value).as_str() {
        "" | "auto" => "auto".to_string(),
        "guarded" | "standard" => "guarded".to_string(),
        "strict" | "locked_down" => "strict".to_string(),
        "cost_guard" | "cost" => "cost_guard".to_string(),
        "sensitive_guard" | "sensitive" => "sensitive_guard".to_string(),
        other => other.to_string(),
    }
}

fn normalize_execution_target(value: &str) -> String {
    match normalize_token(value).as_str() {
        "" | "auto" => "auto".to_string(),
        "cloud" | "hosted" | "hosted_secure" => "cloud".to_string(),
        "local" | "local_companion" | "my_computer" | "gateway" => "local_companion".to_string(),
        other => other.to_string(),
    }
}

fn normalize_elevated_mode(value: &str) -> String {
    match normalize_token(value).as_str() {
        "" | "off" | "false" | "none" => "off".to_string(),
        "request" | "requested" => "request".to_string(),
        "approved" | "true" => "approved".to_string(),
        "session" | "ttl" => "session".to_string(),
        other => other.to_string(),
    }
}

fn normalize_optional_token(value: &str) -> Option<String> {
    let token = normalize_token(value);
    if token.is_empty() {
        None
    } else {
        Some(token)
    }
}

fn normalize_token(value: &str) -> String {
    value
        .trim()
        .to_ascii_lowercase()
        .replace('-', "_")
        .replace('.', "_")
        .replace(' ', "_")
}

fn string_field(input: &Value, key: &str) -> Option<String> {
    input.get(key).and_then(Value::as_str).and_then(|value| {
        let trimmed = value.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_string())
        }
    })
}

fn boolish(input: &Value, key: &str, default_value: bool) -> bool {
    if input.get(key).is_none() {
        return default_value;
    }
    match input.get(key) {
        Some(Value::Bool(value)) => *value,
        Some(Value::String(value)) => matches!(
            normalize_token(value).as_str(),
            "1" | "true" | "yes" | "on" | "enabled" | "active" | "present" | "resolved"
        ),
        Some(Value::Number(value)) => value.as_i64().unwrap_or(0) > 0,
        _ => false,
    }
}

fn positive_i64(input: &Value, key: &str) -> i64 {
    match input.get(key) {
        Some(Value::Number(value)) => value.as_i64().unwrap_or(0),
        Some(Value::String(value)) => value.trim().parse::<i64>().unwrap_or(0),
        _ => 0,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blocks_missing_workspace() {
        let response = run_preparation_decision_command(&json!({
            "operation": "prepare_run_start",
            "tenant_id": "t1"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "workspace_id_missing");
    }

    #[test]
    fn blocks_unsupported_engine() {
        let response = run_preparation_decision_command(&json!({
            "operation": "prepare_run_start",
            "workspace_id": "w1",
            "tenant_id": "t1",
            "engine": "unknown"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "run_engine_unsupported");
    }

    #[test]
    fn blocks_bad_pack_inputs_shape() {
        let response = run_preparation_decision_command(&json!({
            "operation": "prepare_run_start",
            "workspace_id": "w1",
            "tenant_id": "t1",
            "pack_inputs_present": true,
            "pack_inputs_is_object": false
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "metadata_pack_inputs_must_be_object");
    }

    #[test]
    fn elevated_mode_requires_approval() {
        let response = run_preparation_decision_command(&json!({
            "operation": "prepare_run_start",
            "workspace_id": "w1",
            "tenant_id": "t1",
            "elevated_mode": "request"
        }));
        assert_eq!(response["decision"], "require_approval");
        assert_eq!(response["next_action"], "request_elevated_mode_approval");
    }

    #[test]
    fn delegated_child_requires_parent() {
        let response = run_preparation_decision_command(&json!({
            "operation": "prepare_delegated_child",
            "workspace_id": "w1",
            "tenant_id": "t1",
            "agent_role": "builder"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "parent_run_id_missing");
    }

    #[test]
    fn allows_standard_run_preparation() {
        let response = run_preparation_decision_command(&json!({
            "operation": "prepare_run_start",
            "workspace_id": "w1",
            "tenant_id": "t1",
            "engine": "orion",
            "trust_mode": "guarded",
            "execution_target": "cloud"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_action"], "prepare_run_request");
    }
}

#[cfg(test)]
mod run_preparation_kernel_boundary_tests {
    use super::run_preparation_decision_command;

    #[test]
    fn prepare_run_start_rejects_non_object_pack_inputs() {
        let decision = run_preparation_decision_command(&serde_json::json!({
            "operation": "prepare_run_start",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "engine": "orion",
            "pack_inputs_present": true,
            "pack_inputs_is_object": false
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("block")
        );
        assert_eq!(
            decision.get("reason").and_then(|value| value.as_str()),
            Some("metadata_pack_inputs_must_be_object")
        );
    }

    #[test]
    fn elevated_mode_requires_approval_evidence() {
        let decision = run_preparation_decision_command(&serde_json::json!({
            "operation": "prepare_run_start",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "engine": "orion",
            "elevated_mode": "request",
            "elevated_approval_present": false
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("require_approval")
        );
        assert_eq!(
            decision
                .get("approval_required")
                .and_then(|value| value.as_bool()),
            Some(true)
        );
    }
}

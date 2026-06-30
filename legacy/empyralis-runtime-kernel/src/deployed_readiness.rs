use serde_json::{json, Value};

const COMPUTER_RUNTIME_MODES: &[&str] = &["cloud_computer", "my_computer", "self_hosted"];

pub fn deployed_readiness_decision_command(input: &Value) -> Value {
    let stage = normalize_stage(
        string_field(input, "stage")
            .or_else(|| string_field(input, "operation"))
            .or_else(|| string_field(input, "action"))
            .as_deref()
            .unwrap_or("deploy"),
    );
    if stage.is_empty() {
        return block("deployed_readiness_stage_unknown");
    }

    let workspace_id =
        string_field(input, "workspace_id").or_else(|| string_field(input, "owner_workspace_id"));
    let tenant_id = string_field(input, "tenant_id");
    let deployed_agent_id =
        string_field(input, "deployed_agent_id").or_else(|| string_field(input, "agent_id"));
    let mode = normalize_mode(
        string_field(input, "studio_agent_mode")
            .or_else(|| string_field(input, "mode"))
            .as_deref()
            .unwrap_or("text"),
    );
    let current_state = normalize_state(
        string_field(input, "current_state")
            .or_else(|| string_field(input, "deployment_state"))
            .as_deref()
            .unwrap_or("draft"),
    );
    let next_state = normalize_state(string_field(input, "next_state").as_deref().unwrap_or(
        if stage == "deploy" {
            "live"
        } else {
            &current_state
        },
    ));
    let backing_specialist_mode = normalize_token(
        string_field(input, "backing_specialist_mode")
            .as_deref()
            .unwrap_or(""),
    );

    if workspace_id.is_none() {
        return block("workspace_id_missing");
    }
    if tenant_id.is_none() {
        return block("tenant_id_missing");
    }
    if stage != "create" && deployed_agent_id.is_none() {
        return block("deployed_agent_id_missing");
    }
    if !allowed_state_transition(&current_state, &next_state) {
        return block("deployed_agent_state_transition_invalid");
    }

    if stage == "create" {
        return create_quota_decision(
            input,
            stage,
            mode,
            workspace_id,
            tenant_id,
            deployed_agent_id,
        );
    }
    if stage == "test_turn" {
        return test_turn_readiness_decision(
            stage,
            mode,
            workspace_id,
            tenant_id,
            deployed_agent_id,
            &current_state,
        );
    }

    if boolish(input, "backing_install_required", true)
        && !boolish(input, "backing_install_present", false)
    {
        return block("backing_install_missing");
    }
    if boolish(input, "backing_install_present", false) {
        if boolish(input, "backing_install_id_matches", true) == false {
            return block("backing_install_id_mismatch");
        }
        if boolish(input, "tenant_scope_matches", true) == false {
            return block("backing_install_tenant_scope_mismatch");
        }
        if boolish(input, "workspace_scope_matches", true) == false {
            return block("backing_install_workspace_scope_mismatch");
        }
    }
    if positive_i64(input, "unsupported_live_channel_count", 0) > 0 {
        return block("unsupported_live_channels");
    }
    if boolish(input, "customer_live_channel_required", false) {
        if backing_specialist_mode != "customer_live" {
            return block("backing_specialist_customer_live_required");
        }
        if !boolish(input, "customer_live_readiness_passed", false) {
            return block("customer_live_readiness_incomplete");
        }
        if !boolish(input, "live_channel_configuration_present", false) {
            return block("live_channel_configuration_missing");
        }
    } else if !backing_specialist_mode.is_empty()
        && backing_specialist_mode != "owner_test"
        && backing_specialist_mode != "customer_live"
    {
        return block("backing_specialist_mode_invalid");
    }

    if !basic_readiness_complete(input) {
        return block("deployed_agent_required_fields_missing");
    }
    if !boolish(input, "privacy_contract_complete", false) {
        return block("privacy_contract_incomplete");
    }
    if !boolish(input, "privacy_contract_accepted", false) {
        return block("privacy_contract_not_accepted");
    }
    if computer_runtime_required(&mode)
        && !boolish(input, "computer_safety_contract_complete", false)
    {
        return block("computer_safety_contract_incomplete");
    }
    if !boolish(input, "mode_capability_matrix_valid", true) {
        return block("mode_capability_matrix_invalid");
    }
    if !boolish(input, "runtime_eligible", true) {
        return block("runtime_not_eligible");
    }
    if mode == "self_hosted" && !boolish(input, "self_hosted_runtime_binding_present", false) {
        return block("self_hosted_runtime_binding_missing");
    }

    quota_decision(
        input,
        stage,
        mode,
        workspace_id,
        tenant_id,
        deployed_agent_id,
    )
}

fn create_quota_decision(
    input: &Value,
    stage: String,
    mode: String,
    workspace_id: Option<String>,
    tenant_id: Option<String>,
    deployed_agent_id: Option<String>,
) -> Value {
    if quota_exceeded(input, "created_agents_current", "created_agents_max") {
        return block("created_agent_quota_exceeded");
    }
    if quota_exceeded(
        input,
        "mode_created_agents_current",
        "mode_created_agents_max",
    ) {
        return block("mode_created_agent_quota_exceeded");
    }
    allow(
        "deployed_readiness_create_allowed",
        &stage,
        "create_deployed_agent",
        &mode,
        workspace_id,
        tenant_id,
        deployed_agent_id,
        false,
        "security",
    )
}

fn quota_decision(
    input: &Value,
    stage: String,
    mode: String,
    workspace_id: Option<String>,
    tenant_id: Option<String>,
    deployed_agent_id: Option<String>,
) -> Value {
    if stage == "deploy" {
        if quota_exceeded(input, "live_agents_current", "live_agents_max") {
            return block("live_agent_quota_exceeded");
        }
        if quota_exceeded(input, "mode_live_agents_current", "mode_live_agents_max") {
            return block("mode_live_agent_quota_exceeded");
        }
        if quota_exceeded(
            input,
            "concurrent_running_agents_current",
            "concurrent_running_agents_max",
        ) {
            return block("concurrent_running_agent_quota_exceeded");
        }
        if quota_exceeded(
            input,
            "mode_concurrent_running_agents_current",
            "mode_concurrent_running_agents_max",
        ) {
            return block("mode_concurrent_running_agent_quota_exceeded");
        }
    }
    if quota_value_exceeded(input, "daily_message_limit", "messages_per_day_max") {
        return block("daily_message_limit_exceeds_quota");
    }
    if boolish(input, "tools_enabled", false)
        && positive_i64(input, "tool_calls_per_day_max", 0) <= 0
    {
        return block("tool_calls_not_available");
    }
    if quota_float_exceeded(input, "monthly_cost_cap_usd", "monthly_spend_usd_max") {
        return block("monthly_cost_cap_exceeds_quota");
    }
    if mode == "cloud_computer" && positive_i64(input, "runtime_minutes_monthly_max", 0) <= 0 {
        return block("runtime_minutes_not_available");
    }
    if boolish(input, "computer_automation_enabled", false) {
        let sessions_limit = positive_i64(input, "computer_sessions_max", 0);
        if sessions_limit <= 0 {
            return block("computer_sessions_not_available");
        }
        if positive_i64(input, "max_concurrent_sessions", 0) > sessions_limit {
            return block("computer_sessions_limit_exceeded");
        }
    }
    if boolish(input, "memory_enabled", false)
        && positive_i64(input, "storage_memory_size_mb_max", 0) <= 0
    {
        return block("cloud_memory_not_available");
    }
    allow(
        "deployed_readiness_allowed",
        &stage,
        if stage == "deploy" {
            "deploy_deployed_agent"
        } else {
            "update_deployed_agent"
        },
        &mode,
        workspace_id,
        tenant_id,
        deployed_agent_id,
        false,
        "security",
    )
}

fn test_turn_readiness_decision(
    stage: String,
    mode: String,
    workspace_id: Option<String>,
    tenant_id: Option<String>,
    deployed_agent_id: Option<String>,
    current_state: &str,
) -> Value {
    if !matches!(current_state, "draft" | "private_test" | "ready_for_review") {
        return block("deployed_agent_test_turn_state_invalid");
    }
    allow(
        "deployed_readiness_test_turn_allowed",
        &stage,
        "execute_studio_test_turn",
        &mode,
        workspace_id,
        tenant_id,
        deployed_agent_id,
        false,
        "security",
    )
}

fn allow(
    reason: &str,
    stage: &str,
    next_action: &str,
    mode: &str,
    workspace_id: Option<String>,
    tenant_id: Option<String>,
    deployed_agent_id: Option<String>,
    cacheable: bool,
    audit_visibility: &str,
) -> Value {
    json!({
        "ok": true,
        "decision": "allow",
        "reason": reason,
        "stage": stage,
        "next_action": next_action,
        "studio_agent_mode": mode,
        "workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "deployed_agent_id": deployed_agent_id,
        "approval_required": false,
        "cacheable": cacheable,
        "audit_visibility": audit_visibility,
    })
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

fn basic_readiness_complete(input: &Value) -> bool {
    boolish(input, "name_present", false)
        && boolish(input, "persona_present", false)
        && boolish(input, "system_prompt_present", false)
        && boolish(input, "monthly_cost_cap_present", false)
        && boolish(input, "escalation_preset_present", false)
        && boolish(input, "handoff_mode_present", false)
}

fn allowed_state_transition(current: &str, next: &str) -> bool {
    matches!(
        (current, next),
        ("draft", "draft")
            | ("draft", "private_test")
            | ("draft", "ready_for_review")
            | ("draft", "live")
            | ("draft", "archived")
            | ("private_test", "private_test")
            | ("private_test", "ready_for_review")
            | ("private_test", "paused")
            | ("private_test", "archived")
            | ("ready_for_review", "ready_for_review")
            | ("ready_for_review", "private_test")
            | ("ready_for_review", "live")
            | ("ready_for_review", "paused")
            | ("ready_for_review", "archived")
            | ("live", "live")
            | ("live", "paused")
            | ("live", "suspended")
            | ("live", "archived")
            | ("paused", "paused")
            | ("paused", "private_test")
            | ("paused", "ready_for_review")
            | ("paused", "live")
            | ("paused", "suspended")
            | ("paused", "archived")
            | ("suspended", "suspended")
            | ("suspended", "private_test")
            | ("suspended", "ready_for_review")
            | ("suspended", "archived")
            | ("archived", "archived")
    )
}

fn quota_exceeded(input: &Value, current_key: &str, max_key: &str) -> bool {
    let max_value = positive_i64(input, max_key, 0);
    max_value > 0 && positive_i64(input, current_key, 0) >= max_value
}

fn quota_value_exceeded(input: &Value, value_key: &str, max_key: &str) -> bool {
    let value = positive_i64(input, value_key, 0);
    let max_value = positive_i64(input, max_key, 0);
    value > 0 && max_value > 0 && value > max_value
}

fn quota_float_exceeded(input: &Value, value_key: &str, max_key: &str) -> bool {
    let value = positive_f64(input, value_key, 0.0);
    let max_value = positive_f64(input, max_key, 0.0);
    value > 0.0 && max_value > 0.0 && value > max_value
}

fn normalize_stage(value: &str) -> String {
    match normalize_token(value).as_str() {
        "test_turn" | "studio_test" | "private_test_turn" => "test_turn".to_string(),
        "create" | "draft" => "create".to_string(),
        "update" | "patch" => "update".to_string(),
        "deploy" | "go_live" | "live" | "validate" | "readiness" | "validate_live" => {
            "deploy".to_string()
        }
        _ => String::new(),
    }
}

fn normalize_mode(value: &str) -> String {
    match normalize_token(value).as_str() {
        "" | "text" | "chat" | "no_computer" => "text".to_string(),
        "cloud_computer" | "sage_cloud_computer" | "cloud" => "cloud_computer".to_string(),
        "my_computer" | "local_companion" | "local_gateway" => "my_computer".to_string(),
        "self_hosted" | "self_hosted_agent" | "self_host_runtime" => "self_hosted".to_string(),
        other => other.to_string(),
    }
}

fn normalize_state(value: &str) -> String {
    match normalize_token(value).as_str() {
        "" | "draft" => "draft".to_string(),
        "private_test" | "private" => "private_test".to_string(),
        "ready_for_review" | "review" => "ready_for_review".to_string(),
        "live" | "active" => "live".to_string(),
        "paused" => "paused".to_string(),
        "suspended" | "killed" => "suspended".to_string(),
        "archived" => "archived".to_string(),
        other => other.to_string(),
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
            "1" | "true" | "yes" | "on" | "enabled" | "active" | "present" | "passed" | "accepted"
        ),
        Some(Value::Number(value)) => value.as_i64().unwrap_or(0) > 0,
        _ => false,
    }
}

fn positive_i64(input: &Value, key: &str, default_value: i64) -> i64 {
    if input.get(key).is_none() {
        return default_value;
    }
    match input.get(key) {
        Some(Value::Number(value)) => value.as_i64().unwrap_or(default_value).max(0),
        Some(Value::String(value)) => value.trim().parse::<i64>().unwrap_or(default_value).max(0),
        _ => default_value,
    }
}

fn positive_f64(input: &Value, key: &str, default_value: f64) -> f64 {
    if input.get(key).is_none() {
        return default_value;
    }
    match input.get(key) {
        Some(Value::Number(value)) => value.as_f64().unwrap_or(default_value).max(0.0),
        Some(Value::String(value)) => value
            .trim()
            .parse::<f64>()
            .unwrap_or(default_value)
            .max(0.0),
        _ => default_value,
    }
}

fn computer_runtime_required(mode: &str) -> bool {
    COMPUTER_RUNTIME_MODES.contains(&mode)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blocks_invalid_state_transition() {
        let response = deployed_readiness_decision_command(&json!({
            "stage": "deploy",
            "workspace_id": "w1",
            "tenant_id": "t1",
            "deployed_agent_id": "agent1",
            "current_state": "archived",
            "next_state": "live"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(
            response["reason"],
            "deployed_agent_state_transition_invalid"
        );
    }

    #[test]
    fn deploy_requires_backing_install() {
        let response = deployed_readiness_decision_command(&json!({
            "stage": "deploy",
            "workspace_id": "w1",
            "tenant_id": "t1",
            "deployed_agent_id": "agent1",
            "current_state": "ready_for_review",
            "next_state": "live"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "backing_install_missing");
    }

    #[test]
    fn customer_live_channel_requires_customer_live_mode() {
        let response = deployed_readiness_decision_command(&json!({
            "stage": "deploy",
            "workspace_id": "w1",
            "tenant_id": "t1",
            "deployed_agent_id": "agent1",
            "current_state": "ready_for_review",
            "next_state": "live",
            "backing_install_present": true,
            "backing_specialist_mode": "owner_test",
            "customer_live_channel_required": true
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(
            response["reason"],
            "backing_specialist_customer_live_required"
        );
    }

    #[test]
    fn deploy_blocks_live_quota() {
        let response = deployed_readiness_decision_command(&json!({
            "stage": "deploy",
            "workspace_id": "w1",
            "tenant_id": "t1",
            "deployed_agent_id": "agent1",
            "current_state": "ready_for_review",
            "next_state": "live",
            "backing_install_present": true,
            "backing_specialist_mode": "owner_test",
            "name_present": true,
            "persona_present": true,
            "system_prompt_present": true,
            "monthly_cost_cap_present": true,
            "escalation_preset_present": true,
            "handoff_mode_present": true,
            "privacy_contract_complete": true,
            "privacy_contract_accepted": true,
            "live_agents_current": 1,
            "live_agents_max": 1
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "live_agent_quota_exceeded");
    }

    #[test]
    fn deploy_allows_ready_text_agent() {
        let response = deployed_readiness_decision_command(&json!({
            "stage": "deploy",
            "workspace_id": "w1",
            "tenant_id": "t1",
            "deployed_agent_id": "agent1",
            "current_state": "ready_for_review",
            "next_state": "live",
            "backing_install_present": true,
            "backing_specialist_mode": "owner_test",
            "name_present": true,
            "persona_present": true,
            "system_prompt_present": true,
            "monthly_cost_cap_present": true,
            "escalation_preset_present": true,
            "handoff_mode_present": true,
            "privacy_contract_complete": true,
            "privacy_contract_accepted": true
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_action"], "deploy_deployed_agent");
    }
}

#[cfg(test)]
mod deployed_readiness_test_turn_kernel_boundary_tests {
    use super::deployed_readiness_decision_command;

    #[test]
    fn test_turn_allows_private_test_state() {
        let decision = deployed_readiness_decision_command(&serde_json::json!({
            "stage": "test_turn",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "deployed_agent_id": "dagent_1",
            "studio_agent_mode": "text_agent",
            "current_state": "private_test",
            "next_state": "private_test"
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("allow")
        );
        assert_eq!(
            decision.get("next_action").and_then(|value| value.as_str()),
            Some("execute_studio_test_turn")
        );
    }

    #[test]
    fn test_turn_blocks_live_state() {
        let decision = deployed_readiness_decision_command(&serde_json::json!({
            "stage": "test_turn",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "deployed_agent_id": "dagent_1",
            "studio_agent_mode": "text_agent",
            "current_state": "live",
            "next_state": "live"
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("block")
        );
        assert_eq!(
            decision.get("reason").and_then(|value| value.as_str()),
            Some("deployed_agent_test_turn_state_invalid")
        );
    }
}

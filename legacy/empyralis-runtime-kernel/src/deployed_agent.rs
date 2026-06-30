use serde_json::{json, Value};

const DEPLOYED_AGENT_STATES: &[&str] = &[
    "draft",
    "private_test",
    "ready_for_review",
    "live",
    "paused",
    "suspended",
    "archived",
];

const COMPUTER_RUNTIME_MODES: &[&str] = &["cloud_computer", "my_computer", "self_hosted"];

pub fn deployed_agent_decision_command(input: &Value) -> Value {
    let operation = normalize_operation(
        string_field(input, "operation")
            .or_else(|| string_field(input, "action"))
            .or_else(|| string_field(input, "intent"))
            .as_deref()
            .unwrap_or(""),
    );
    if operation.is_empty() {
        return block("deployed_agent_operation_missing");
    }

    let actor_role = normalize_actor_role(
        string_field(input, "actor_role")
            .or_else(|| string_field(input, "role"))
            .as_deref()
            .unwrap_or("member"),
    );
    let workspace_id =
        string_field(input, "workspace_id").or_else(|| string_field(input, "owner_workspace_id"));
    let deployed_agent_id = string_field(input, "deployed_agent_id")
        .or_else(|| string_field(input, "agent_id"))
        .or_else(|| string_field(input, "id"));
    let state = normalize_state(
        string_field(input, "deployment_state")
            .or_else(|| string_field(input, "current_state"))
            .or_else(|| string_field(input, "state"))
            .as_deref()
            .unwrap_or("draft"),
    );
    let mode = normalize_mode(
        string_field(input, "studio_agent_mode")
            .or_else(|| string_field(input, "runtime_target"))
            .or_else(|| string_field(input, "mode"))
            .as_deref()
            .unwrap_or("text"),
    );

    if workspace_id.is_none() {
        return block("workspace_id_missing");
    }
    if !DEPLOYED_AGENT_STATES.contains(&state.as_str()) {
        return block("deployed_agent_state_unknown");
    }

    if matches!(operation.as_str(), "read" | "list" | "project") {
        return allow(
            "deployed_agent_read_allowed",
            &operation,
            "read_agent",
            &state,
            &mode,
            workspace_id,
            deployed_agent_id,
            &actor_role,
            true,
            "standard",
        );
    }

    if !actor_can_write(&actor_role) {
        return block("deployed_agent_actor_cannot_write");
    }

    if requires_existing_agent(&operation) && deployed_agent_id.is_none() {
        return block("deployed_agent_id_missing");
    }
    if state == "archived" && operation != "recover" {
        return block("deployed_agent_archived");
    }

    match operation.as_str() {
        "create_draft" => allow(
            "deployed_agent_draft_create_allowed",
            &operation,
            "create_draft",
            &state,
            &mode,
            workspace_id,
            deployed_agent_id,
            &actor_role,
            false,
            "security",
        ),
        "update" => {
            if matches!(state.as_str(), "suspended") && actor_role != "system" {
                return require_approval(
                    "deployed_agent_suspended_update_requires_approval",
                    &operation,
                    "request_owner_approval",
                    &state,
                    &mode,
                    workspace_id,
                    deployed_agent_id,
                    &actor_role,
                );
            }
            allow(
                "deployed_agent_update_allowed",
                &operation,
                "update_agent",
                &state,
                &mode,
                workspace_id,
                deployed_agent_id,
                &actor_role,
                false,
                "security",
            )
        }
        "deploy" => deploy_decision(
            input,
            state,
            mode,
            workspace_id,
            deployed_agent_id,
            actor_role,
        ),
        "pause" => lifecycle_transition(
            "deployed_agent_pause_allowed",
            &operation,
            "pause_agent",
            &state,
            &mode,
            workspace_id,
            deployed_agent_id,
            actor_role,
            &["live", "private_test", "ready_for_review"],
        ),
        "kill" => {
            if actor_role == "system" || boolish(input, "emergency_stop_confirmed", false) {
                allow(
                    "deployed_agent_kill_allowed",
                    &operation,
                    "kill_agent_runtime",
                    &state,
                    &mode,
                    workspace_id,
                    deployed_agent_id,
                    &actor_role,
                    false,
                    "security",
                )
            } else {
                require_approval(
                    "deployed_agent_kill_requires_approval",
                    &operation,
                    "request_owner_approval",
                    &state,
                    &mode,
                    workspace_id,
                    deployed_agent_id,
                    &actor_role,
                )
            }
        }
        "recover" => lifecycle_transition(
            "deployed_agent_recover_allowed",
            &operation,
            "recover_agent",
            &state,
            &mode,
            workspace_id,
            deployed_agent_id,
            actor_role,
            &["paused", "suspended", "archived"],
        ),
        "archive" => {
            if matches!(state.as_str(), "live") && actor_role != "system" {
                return require_approval(
                    "deployed_agent_live_archive_requires_approval",
                    &operation,
                    "request_owner_approval",
                    &state,
                    &mode,
                    workspace_id,
                    deployed_agent_id,
                    &actor_role,
                );
            }
            lifecycle_transition(
                "deployed_agent_archive_allowed",
                &operation,
                "archive_agent",
                &state,
                &mode,
                workspace_id,
                deployed_agent_id,
                actor_role,
                &[
                    "draft",
                    "private_test",
                    "ready_for_review",
                    "paused",
                    "suspended",
                ],
            )
        }
        "public_route" => {
            public_route_decision(state, mode, workspace_id, deployed_agent_id, actor_role)
        }
        "runtime_session" => runtime_session_decision(
            input,
            state,
            mode,
            workspace_id,
            deployed_agent_id,
            actor_role,
        ),
        "runtime_action" => runtime_action_decision(
            input,
            state,
            mode,
            workspace_id,
            deployed_agent_id,
            actor_role,
        ),
        _ => block("deployed_agent_operation_unknown"),
    }
}

fn deploy_decision(
    input: &Value,
    state: String,
    mode: String,
    workspace_id: Option<String>,
    deployed_agent_id: Option<String>,
    actor_role: String,
) -> Value {
    if !matches!(
        state.as_str(),
        "draft" | "private_test" | "ready_for_review" | "paused"
    ) {
        return block("deployed_agent_state_not_deployable");
    }
    if !boolish(input, "privacy_contract_complete", false) {
        return block("privacy_contract_incomplete");
    }
    if computer_runtime_required(&mode)
        && !boolish(input, "computer_safety_contract_complete", false)
    {
        return block("computer_safety_contract_incomplete");
    }
    if boolish(input, "full_runtime_access_requested", false)
        && !boolish(input, "owner_approval_accepted", false)
        && actor_role != "system"
    {
        return require_approval(
            "deployed_agent_full_runtime_access_requires_approval",
            "deploy",
            "request_owner_approval",
            &state,
            &mode,
            workspace_id,
            deployed_agent_id,
            &actor_role,
        );
    }
    if boolish(input, "budget_exhausted", false) {
        return block("deployed_agent_budget_exhausted");
    }
    if boolish(input, "requires_live_channel", false)
        && !boolish(input, "live_channel_configured", false)
    {
        return block("deployed_agent_live_channel_missing");
    }
    allow(
        "deployed_agent_deploy_allowed",
        "deploy",
        "deploy_agent",
        &state,
        &mode,
        workspace_id,
        deployed_agent_id,
        &actor_role,
        false,
        "security",
    )
}

fn public_route_decision(
    state: String,
    mode: String,
    workspace_id: Option<String>,
    deployed_agent_id: Option<String>,
    actor_role: String,
) -> Value {
    match state.as_str() {
        "live" => allow(
            "deployed_agent_public_route_allowed",
            "public_route",
            "route_live_agent",
            &state,
            &mode,
            workspace_id,
            deployed_agent_id,
            &actor_role,
            false,
            "standard",
        ),
        "paused" => allow(
            "deployed_agent_public_route_paused_reply",
            "public_route",
            "route_paused_reply",
            &state,
            &mode,
            workspace_id,
            deployed_agent_id,
            &actor_role,
            false,
            "standard",
        ),
        "suspended" => allow(
            "deployed_agent_public_route_suspended_reply",
            "public_route",
            "route_suspended_reply",
            &state,
            &mode,
            workspace_id,
            deployed_agent_id,
            &actor_role,
            false,
            "standard",
        ),
        _ => block("deployed_agent_not_publicly_routable"),
    }
}

fn runtime_session_decision(
    input: &Value,
    state: String,
    mode: String,
    workspace_id: Option<String>,
    deployed_agent_id: Option<String>,
    actor_role: String,
) -> Value {
    if deployed_agent_id.is_none() {
        return block("deployed_agent_id_missing");
    }
    if state != "live" && state != "private_test" {
        return block("deployed_agent_runtime_state_blocked");
    }
    if boolish(input, "kill_switch_active", false) {
        return block("deployed_agent_kill_switch_active");
    }
    if !computer_runtime_required(&mode) {
        return allow(
            "deployed_agent_text_runtime_session_noop",
            "runtime_session",
            "skip_runtime_session",
            &state,
            &mode,
            workspace_id,
            deployed_agent_id,
            &actor_role,
            false,
            "standard",
        );
    }
    if mode == "self_hosted" && !boolish(input, "runtime_binding_present", false) {
        return block("self_hosted_runtime_binding_missing");
    }
    if mode == "my_computer" && !boolish(input, "gateway_healthy", false) {
        return block("my_computer_gateway_unhealthy");
    }
    allow(
        "deployed_agent_runtime_session_allowed",
        "runtime_session",
        "create_runtime_session",
        &state,
        &mode,
        workspace_id,
        deployed_agent_id,
        &actor_role,
        false,
        "security",
    )
}

fn runtime_action_decision(
    input: &Value,
    state: String,
    mode: String,
    workspace_id: Option<String>,
    deployed_agent_id: Option<String>,
    actor_role: String,
) -> Value {
    if deployed_agent_id.is_none() {
        return block("deployed_agent_id_missing");
    }
    if state != "live" && state != "private_test" {
        return block("deployed_agent_runtime_action_state_blocked");
    }
    if boolish(input, "kill_switch_active", false) {
        return block("deployed_agent_kill_switch_active");
    }
    if boolish(input, "raw_policy_override_present", false) {
        return block("deployed_agent_raw_policy_override_blocked");
    }
    if boolish(input, "requires_owner_approval", false)
        && !boolish(input, "owner_approval_accepted", false)
    {
        return require_approval(
            "deployed_agent_runtime_action_requires_approval",
            "runtime_action",
            "request_owner_approval",
            &state,
            &mode,
            workspace_id,
            deployed_agent_id,
            &actor_role,
        );
    }
    allow(
        "deployed_agent_runtime_action_allowed",
        "runtime_action",
        "execute_runtime_action",
        &state,
        &mode,
        workspace_id,
        deployed_agent_id,
        &actor_role,
        false,
        "security",
    )
}

fn lifecycle_transition(
    reason: &str,
    operation: &str,
    next_action: &str,
    state: &str,
    mode: &str,
    workspace_id: Option<String>,
    deployed_agent_id: Option<String>,
    actor_role: String,
    allowed_from: &[&str],
) -> Value {
    if !allowed_from.contains(&state) {
        return block("deployed_agent_state_transition_invalid");
    }
    allow(
        reason,
        operation,
        next_action,
        state,
        mode,
        workspace_id,
        deployed_agent_id,
        &actor_role,
        false,
        "security",
    )
}

fn allow(
    reason: &str,
    operation: &str,
    next_action: &str,
    state: &str,
    mode: &str,
    workspace_id: Option<String>,
    deployed_agent_id: Option<String>,
    actor_role: &str,
    cacheable: bool,
    audit_visibility: &str,
) -> Value {
    response(
        "allow",
        reason,
        operation,
        next_action,
        state,
        mode,
        workspace_id,
        deployed_agent_id,
        actor_role,
        false,
        cacheable,
        audit_visibility,
    )
}

fn require_approval(
    reason: &str,
    operation: &str,
    next_action: &str,
    state: &str,
    mode: &str,
    workspace_id: Option<String>,
    deployed_agent_id: Option<String>,
    actor_role: &str,
) -> Value {
    response(
        "require_approval",
        reason,
        operation,
        next_action,
        state,
        mode,
        workspace_id,
        deployed_agent_id,
        actor_role,
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
    state: &str,
    mode: &str,
    workspace_id: Option<String>,
    deployed_agent_id: Option<String>,
    actor_role: &str,
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
        "deployment_state": state,
        "studio_agent_mode": mode,
        "workspace_id": workspace_id,
        "deployed_agent_id": deployed_agent_id,
        "actor_role": actor_role,
        "approval_required": approval_required || decision == "require_approval",
        "cacheable": cacheable,
        "audit_visibility": audit_visibility,
    })
}

fn normalize_operation(value: &str) -> String {
    match normalize_token(value).as_str() {
        "read" | "get" | "detail" => "read".to_string(),
        "list" | "search" => "list".to_string(),
        "project" | "projection" => "project".to_string(),
        "create" | "create_draft" | "draft" => "create_draft".to_string(),
        "update" | "patch" => "update".to_string(),
        "deploy" | "go_live" | "publish" => "deploy".to_string(),
        "pause" => "pause".to_string(),
        "kill" | "emergency_stop" | "stop" => "kill".to_string(),
        "recover" | "resume" => "recover".to_string(),
        "archive" => "archive".to_string(),
        "public_route" | "route_public" | "channel_route" => "public_route".to_string(),
        "runtime_session" | "create_runtime_session" | "bind_runtime" => {
            "runtime_session".to_string()
        }
        "runtime_action" | "tool_action" | "execute_runtime_action" => "runtime_action".to_string(),
        _ => String::new(),
    }
}

fn normalize_state(value: &str) -> String {
    match normalize_token(value).as_str() {
        "draft" => "draft".to_string(),
        "private_test" | "private" | "test" => "private_test".to_string(),
        "ready_for_review" | "review" => "ready_for_review".to_string(),
        "live" | "active" => "live".to_string(),
        "paused" | "pause" => "paused".to_string(),
        "suspended" | "killed" | "stopped" => "suspended".to_string(),
        "archived" | "archive" => "archived".to_string(),
        _ => String::new(),
    }
}

fn normalize_mode(value: &str) -> String {
    match normalize_token(value).as_str() {
        "text" | "chat" | "no_computer" => "text".to_string(),
        "cloud_computer" | "sage_cloud_computer" | "cloud" => "cloud_computer".to_string(),
        "my_computer" | "local_companion" | "local" => "my_computer".to_string(),
        "self_hosted" | "self_hosted_agent" | "self_host_runtime" => "self_hosted".to_string(),
        _ => "text".to_string(),
    }
}

fn normalize_actor_role(value: &str) -> String {
    match normalize_token(value).as_str() {
        "system" | "service" => "system".to_string(),
        "owner" | "admin" => "owner".to_string(),
        "editor" | "maintainer" | "member" => "member".to_string(),
        "viewer" | "readonly" | "read_only" => "viewer".to_string(),
        _ => "member".to_string(),
    }
}

fn normalize_token(value: &str) -> String {
    value
        .trim()
        .to_ascii_lowercase()
        .replace(['-', '.', ' '], "_")
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
            "1" | "true" | "yes" | "on" | "enabled" | "active" | "accepted" | "complete"
        ),
        Some(Value::Number(value)) => value.as_u64().unwrap_or(0) > 0,
        _ => false,
    }
}

fn actor_can_write(actor_role: &str) -> bool {
    matches!(actor_role, "system" | "owner" | "member")
}

fn requires_existing_agent(operation: &str) -> bool {
    !matches!(operation, "create_draft" | "list")
}

fn computer_runtime_required(mode: &str) -> bool {
    COMPUTER_RUNTIME_MODES.contains(&mode)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blocks_missing_operation() {
        let response = deployed_agent_decision_command(&json!({"workspace_id": "w1"}));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "deployed_agent_operation_missing");
    }

    #[test]
    fn deploy_blocks_missing_privacy_contract() {
        let response = deployed_agent_decision_command(&json!({
            "operation": "deploy",
            "workspace_id": "w1",
            "deployed_agent_id": "agent1",
            "deployment_state": "ready_for_review"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "privacy_contract_incomplete");
    }

    #[test]
    fn full_runtime_access_deploy_requires_approval() {
        let response = deployed_agent_decision_command(&json!({
            "operation": "deploy",
            "workspace_id": "w1",
            "deployed_agent_id": "agent1",
            "deployment_state": "ready_for_review",
            "studio_agent_mode": "cloud_computer",
            "privacy_contract_complete": true,
            "computer_safety_contract_complete": true,
            "full_runtime_access_requested": true
        }));
        assert_eq!(response["decision"], "require_approval");
    }

    #[test]
    fn live_public_route_is_allowed() {
        let response = deployed_agent_decision_command(&json!({
            "operation": "public_route",
            "workspace_id": "w1",
            "deployed_agent_id": "agent1",
            "deployment_state": "live"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_action"], "route_live_agent");
    }

    #[test]
    fn my_computer_runtime_requires_healthy_gateway() {
        let response = deployed_agent_decision_command(&json!({
            "operation": "runtime_session",
            "workspace_id": "w1",
            "deployed_agent_id": "agent1",
            "deployment_state": "live",
            "studio_agent_mode": "my_computer"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "my_computer_gateway_unhealthy");
    }

    #[test]
    fn runtime_action_blocks_raw_policy_override() {
        let response = deployed_agent_decision_command(&json!({
            "operation": "runtime_action",
            "workspace_id": "w1",
            "deployed_agent_id": "agent1",
            "deployment_state": "live",
            "raw_policy_override_present": true
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(
            response["reason"],
            "deployed_agent_raw_policy_override_blocked"
        );
    }
}

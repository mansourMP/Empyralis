use serde_json::{json, Value};

const LOCAL_RUNTIME_MODES: &[&str] = &["local_secure", "local_unrestricted"];
const HOSTED_RUNTIME_MODES: &[&str] = &["hosted_secure", "hosted_unrestricted"];
const EXECUTION_TARGETS: &[&str] = &["auto", "cloud", "local_companion"];

pub fn run_routing_decision_command(input: &Value) -> Value {
    let operation = normalize_operation(
        string_field(input, "operation")
            .or_else(|| string_field(input, "action"))
            .or_else(|| string_field(input, "intent"))
            .as_deref()
            .unwrap_or(""),
    );
    if operation.is_empty() {
        return block("run_routing_operation_missing");
    }

    let workspace_id = string_field(input, "workspace_id");
    let run_id = string_field(input, "run_id").or_else(|| string_field(input, "id"));
    let schedule_id = string_field(input, "schedule_id");
    let parent_run_id = string_field(input, "parent_run_id");
    let execution_target = normalize_execution_target(
        string_field(input, "execution_target_selected")
            .or_else(|| string_field(input, "execution_target"))
            .or_else(|| string_field(input, "selected_target"))
            .as_deref()
            .unwrap_or("auto"),
    );
    let runtime_mode = normalize_runtime_mode(
        string_field(input, "runtime_mode")
            .or_else(|| string_field(input, "mode"))
            .as_deref()
            .unwrap_or(""),
    );

    if workspace_id.is_none() {
        return block("workspace_id_missing");
    }
    if !EXECUTION_TARGETS.contains(&execution_target.as_str()) {
        return block("run_routing_execution_target_unknown");
    }

    match operation.as_str() {
        "execution_boundary" => execution_boundary_decision(
            input,
            workspace_id,
            run_id,
            schedule_id,
            parent_run_id,
            execution_target,
            runtime_mode,
        ),
        "routing_preview" => routing_preview_decision(
            input,
            workspace_id,
            run_id,
            schedule_id,
            parent_run_id,
            execution_target,
            runtime_mode,
        ),
        "local_confirmation" => local_confirmation_decision(
            input,
            workspace_id,
            run_id,
            schedule_id,
            parent_run_id,
            execution_target,
            runtime_mode,
        ),
        "delegation_child" => delegation_child_decision(
            input,
            workspace_id,
            run_id,
            schedule_id,
            parent_run_id,
            execution_target,
            runtime_mode,
        ),
        "delegation_merge" => delegation_merge_decision(
            input,
            workspace_id,
            run_id,
            schedule_id,
            parent_run_id,
            execution_target,
            runtime_mode,
        ),
        _ => block("run_routing_operation_unknown"),
    }
}

fn execution_boundary_decision(
    input: &Value,
    workspace_id: Option<String>,
    run_id: Option<String>,
    schedule_id: Option<String>,
    parent_run_id: Option<String>,
    execution_target: String,
    runtime_mode: String,
) -> Value {
    let selected_target = resolved_target(&execution_target, input);
    let resolved_mode = resolved_runtime_mode(&runtime_mode, &selected_target);
    if !runtime_mode_matches_target(&resolved_mode, &selected_target) {
        return block("run_routing_runtime_mode_target_mismatch");
    }

    let runtime_selection_present = boolish(input, "runtime_selection_present", false)
        || input.get("runtime_selection").is_some();
    let runtime_binding_requested = boolish(input, "runtime_binding_requested", false)
        || string_field(input, "runtime_attachment_id").is_some()
        || string_field(input, "runtime_attachment_kind").is_some()
        || string_field(input, "workspace_runtime_deployment_mode").is_some();

    if runtime_selection_present {
        if string_field(input, "runtime_attachment_id").is_none() {
            return block("runtime_attachment_id_missing");
        }
        if string_field(input, "runtime_attachment_kind").is_none() {
            return block("runtime_attachment_kind_missing");
        }
        let selection_target = normalize_execution_target(
            string_field(input, "runtime_selection_target")
                .or_else(|| string_field(input, "selection_target"))
                .as_deref()
                .unwrap_or(&selected_target),
        );
        if selection_target != selected_target {
            return block("runtime_selection_target_mismatch");
        }
        if is_local_mode(&resolved_mode) && string_field(input, "machine_target").is_none() {
            return block("local_runtime_machine_target_missing");
        }
    } else if runtime_binding_requested
        || (selected_target == "local_companion" && string_field(input, "machine_target").is_some())
    {
        return block("runtime_attachment_selection_required");
    }

    allow(
        "run_execution_boundary_allowed",
        "execution_boundary",
        "write_execution_boundary_metadata",
        &selected_target,
        &resolved_mode,
        workspace_id,
        run_id,
        schedule_id,
        parent_run_id,
        false,
        "security",
    )
}

fn routing_preview_decision(
    input: &Value,
    workspace_id: Option<String>,
    run_id: Option<String>,
    schedule_id: Option<String>,
    parent_run_id: Option<String>,
    execution_target: String,
    runtime_mode: String,
) -> Value {
    if boolish(input, "workflow_required", false)
        && !boolish(input, "workflow_snapshot_present", false)
    {
        return block("workflow_snapshot_missing");
    }
    let selected_target = resolved_target(&execution_target, input);
    let resolved_mode = resolved_runtime_mode(&runtime_mode, &selected_target);
    allow(
        "run_routing_preview_allowed",
        "routing_preview",
        "build_routing_preview",
        &selected_target,
        &resolved_mode,
        workspace_id,
        run_id,
        schedule_id,
        parent_run_id,
        true,
        "standard",
    )
}

fn local_confirmation_decision(
    input: &Value,
    workspace_id: Option<String>,
    run_id: Option<String>,
    schedule_id: Option<String>,
    parent_run_id: Option<String>,
    execution_target: String,
    runtime_mode: String,
) -> Value {
    let selected_target = resolved_target(&execution_target, input);
    let resolved_mode = resolved_runtime_mode(&runtime_mode, &selected_target);
    if selected_target != "local_companion" {
        return allow(
            "local_confirmation_not_required_for_target",
            "local_confirmation",
            "continue_without_confirmation",
            &selected_target,
            &resolved_mode,
            workspace_id,
            run_id,
            schedule_id,
            parent_run_id,
            false,
            "standard",
        );
    }
    if normalize_token(string_field(input, "outcome_pack").as_deref().unwrap_or(""))
        != "local_execution"
    {
        return allow(
            "local_confirmation_not_required_for_pack",
            "local_confirmation",
            "continue_without_confirmation",
            &selected_target,
            &resolved_mode,
            workspace_id,
            run_id,
            schedule_id,
            parent_run_id,
            false,
            "standard",
        );
    }
    if positive_i64(input, "blocked_count") > 0 {
        return block("local_execution_precheck_blocked");
    }
    if positive_i64(input, "require_confirmation_count") > 0
        || positive_i64(input, "approval_required_count") > 0
    {
        return require_approval(
            "local_execution_requires_confirmation",
            "local_confirmation",
            "request_local_execution_confirmation",
            &selected_target,
            &resolved_mode,
            workspace_id,
            run_id,
            schedule_id,
            parent_run_id,
        );
    }
    allow(
        "local_execution_confirmation_not_required",
        "local_confirmation",
        "continue_local_execution",
        &selected_target,
        &resolved_mode,
        workspace_id,
        run_id,
        schedule_id,
        parent_run_id,
        false,
        "security",
    )
}

fn delegation_child_decision(
    input: &Value,
    workspace_id: Option<String>,
    run_id: Option<String>,
    schedule_id: Option<String>,
    parent_run_id: Option<String>,
    execution_target: String,
    runtime_mode: String,
) -> Value {
    if parent_run_id.is_none() {
        return block("parent_run_id_missing");
    }
    if string_field(input, "agent_role").is_none() {
        return block("delegated_agent_role_missing");
    }
    if !has_user_goal(input) {
        return block("delegated_user_goal_missing");
    }
    if positive_i64(input, "workflow_turn_depth") > positive_i64(input, "max_workflow_turn_depth") {
        return block("workflow_turn_depth_exceeded");
    }
    let selected_target = resolved_target(&execution_target, input);
    let resolved_mode = resolved_runtime_mode(&runtime_mode, &selected_target);
    allow(
        "delegation_child_route_allowed",
        "delegation_child",
        "create_delegated_child_run",
        &selected_target,
        &resolved_mode,
        workspace_id,
        run_id,
        schedule_id,
        parent_run_id,
        false,
        "security",
    )
}

fn delegation_merge_decision(
    input: &Value,
    workspace_id: Option<String>,
    run_id: Option<String>,
    schedule_id: Option<String>,
    parent_run_id: Option<String>,
    execution_target: String,
    runtime_mode: String,
) -> Value {
    if positive_i64(input, "active_children") > 0 {
        return allow(
            "delegation_merge_waiting_for_children",
            "delegation_merge",
            "wait_for_children",
            &resolved_target(&execution_target, input),
            &resolved_runtime_mode(&runtime_mode, &resolved_target(&execution_target, input)),
            workspace_id,
            run_id,
            schedule_id,
            parent_run_id,
            false,
            "standard",
        );
    }
    if positive_i64(input, "waiting_children") > 0 {
        return require_approval(
            "delegation_merge_waiting_for_child_approval",
            "delegation_merge",
            "resolve_child_approvals",
            &resolved_target(&execution_target, input),
            &resolved_runtime_mode(&runtime_mode, &resolved_target(&execution_target, input)),
            workspace_id,
            run_id,
            schedule_id,
            parent_run_id,
        );
    }
    if positive_i64(input, "failed_children") > 0 {
        return require_approval(
            "delegation_merge_failed_children_need_retry",
            "delegation_merge",
            "retry_failed_children",
            &resolved_target(&execution_target, input),
            &resolved_runtime_mode(&runtime_mode, &resolved_target(&execution_target, input)),
            workspace_id,
            run_id,
            schedule_id,
            parent_run_id,
        );
    }
    allow(
        "delegation_merge_ready",
        "delegation_merge",
        "merge_child_results",
        &resolved_target(&execution_target, input),
        &resolved_runtime_mode(&runtime_mode, &resolved_target(&execution_target, input)),
        workspace_id,
        run_id,
        schedule_id,
        parent_run_id,
        false,
        "security",
    )
}

fn allow(
    reason: &str,
    operation: &str,
    next_action: &str,
    execution_target: &str,
    runtime_mode: &str,
    workspace_id: Option<String>,
    run_id: Option<String>,
    schedule_id: Option<String>,
    parent_run_id: Option<String>,
    cacheable: bool,
    audit_visibility: &str,
) -> Value {
    response(
        "allow",
        reason,
        operation,
        next_action,
        execution_target,
        runtime_mode,
        workspace_id,
        run_id,
        schedule_id,
        parent_run_id,
        false,
        cacheable,
        audit_visibility,
    )
}

fn require_approval(
    reason: &str,
    operation: &str,
    next_action: &str,
    execution_target: &str,
    runtime_mode: &str,
    workspace_id: Option<String>,
    run_id: Option<String>,
    schedule_id: Option<String>,
    parent_run_id: Option<String>,
) -> Value {
    response(
        "require_approval",
        reason,
        operation,
        next_action,
        execution_target,
        runtime_mode,
        workspace_id,
        run_id,
        schedule_id,
        parent_run_id,
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
    execution_target: &str,
    runtime_mode: &str,
    workspace_id: Option<String>,
    run_id: Option<String>,
    schedule_id: Option<String>,
    parent_run_id: Option<String>,
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
        "execution_target": execution_target,
        "runtime_mode": runtime_mode,
        "workspace_id": workspace_id,
        "run_id": run_id,
        "schedule_id": schedule_id,
        "parent_run_id": parent_run_id,
        "approval_required": approval_required || decision == "require_approval",
        "cacheable": cacheable,
        "audit_visibility": audit_visibility,
    })
}

fn normalize_operation(value: &str) -> String {
    match normalize_token(value).as_str() {
        "execution_boundary" | "resolve_execution_boundary" | "route_boundary" => {
            "execution_boundary".to_string()
        }
        "routing_preview" | "preview" | "run_preview" => "routing_preview".to_string(),
        "local_confirmation" | "local_execution_confirmation" => "local_confirmation".to_string(),
        "delegation_child" | "child_run" | "create_child_run" => "delegation_child".to_string(),
        "delegation_merge" | "refresh_parent_delegation" | "merge_children" => {
            "delegation_merge".to_string()
        }
        _ => String::new(),
    }
}

fn normalize_execution_target(value: &str) -> String {
    match normalize_token(value).as_str() {
        "" | "auto" => "auto".to_string(),
        "cloud" | "hosted" | "hosted_secure" | "sage_cloud" => "cloud".to_string(),
        "local" | "local_companion" | "my_computer" | "gateway" => "local_companion".to_string(),
        _ => String::new(),
    }
}

fn normalize_runtime_mode(value: &str) -> String {
    match normalize_token(value).as_str() {
        "local_secure" | "local_unrestricted" | "hosted_secure" | "hosted_unrestricted" => {
            normalize_token(value)
        }
        _ => String::new(),
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
            "1" | "true" | "yes" | "on" | "enabled" | "active" | "present"
        ),
        Some(Value::Number(value)) => value.as_i64().unwrap_or(0) > 0,
        _ => false,
    }
}

fn positive_i64(input: &Value, key: &str) -> i64 {
    match input.get(key) {
        Some(Value::Number(value)) => value.as_i64().unwrap_or(0).max(0),
        Some(Value::String(value)) => value.trim().parse::<i64>().unwrap_or(0).max(0),
        _ => 0,
    }
}

fn resolved_target(execution_target: &str, input: &Value) -> String {
    if execution_target == "auto" {
        if boolish(input, "prefer_local", false) {
            "local_companion".to_string()
        } else {
            "cloud".to_string()
        }
    } else {
        execution_target.to_string()
    }
}

fn resolved_runtime_mode(runtime_mode: &str, selected_target: &str) -> String {
    if !runtime_mode.is_empty() {
        return runtime_mode.to_string();
    }
    if selected_target == "local_companion" {
        "local_secure".to_string()
    } else {
        "hosted_secure".to_string()
    }
}

fn is_local_mode(runtime_mode: &str) -> bool {
    LOCAL_RUNTIME_MODES.contains(&runtime_mode)
}

fn runtime_mode_matches_target(runtime_mode: &str, selected_target: &str) -> bool {
    if selected_target == "local_companion" {
        LOCAL_RUNTIME_MODES.contains(&runtime_mode)
    } else {
        HOSTED_RUNTIME_MODES.contains(&runtime_mode)
    }
}

fn has_user_goal(input: &Value) -> bool {
    string_field(input, "user_goal")
        .or_else(|| string_field(input, "objective"))
        .or_else(|| string_field(input, "prompt"))
        .is_some()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blocks_missing_operation() {
        let response = run_routing_decision_command(&json!({"workspace_id": "w1"}));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "run_routing_operation_missing");
    }

    #[test]
    fn blocks_runtime_mode_target_mismatch() {
        let response = run_routing_decision_command(&json!({
            "operation": "execution_boundary",
            "workspace_id": "w1",
            "execution_target": "local_companion",
            "runtime_mode": "hosted_secure"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(
            response["reason"],
            "run_routing_runtime_mode_target_mismatch"
        );
    }

    #[test]
    fn requires_runtime_attachment_selection_for_binding() {
        let response = run_routing_decision_command(&json!({
            "operation": "execution_boundary",
            "workspace_id": "w1",
            "execution_target": "cloud",
            "runtime_binding_requested": true
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "runtime_attachment_selection_required");
    }

    #[test]
    fn local_confirmation_requires_approval_for_precheck() {
        let response = run_routing_decision_command(&json!({
            "operation": "local_confirmation",
            "workspace_id": "w1",
            "execution_target": "local_companion",
            "outcome_pack": "local_execution",
            "require_confirmation_count": 1
        }));
        assert_eq!(response["decision"], "require_approval");
        assert_eq!(
            response["next_action"],
            "request_local_execution_confirmation"
        );
    }

    #[test]
    fn delegation_child_requires_parent() {
        let response = run_routing_decision_command(&json!({
            "operation": "delegation_child",
            "workspace_id": "w1",
            "agent_role": "builder",
            "user_goal": "Build"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "parent_run_id_missing");
    }

    #[test]
    fn delegation_merge_waits_for_active_children() {
        let response = run_routing_decision_command(&json!({
            "operation": "delegation_merge",
            "workspace_id": "w1",
            "run_id": "parent",
            "active_children": 2
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_action"], "wait_for_children");
    }
}

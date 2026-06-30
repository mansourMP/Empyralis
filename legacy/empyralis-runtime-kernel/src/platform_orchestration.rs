use serde_json::{json, Value};

const OPERATIONS: &[&str] = &[
    "control_plane_record",
    "workspace_access",
    "tenant_bootstrap",
    "thread_turn",
    "run_create",
    "run_dispatch",
    "run_stream",
    "run_cancel",
    "run_retry",
    "workflow_child",
    "delegation_merge",
    "gateway_route",
    "gateway_tool",
    "gateway_browser",
    "deployed_agent_action",
    "deployed_agent_runtime",
    "billing_entitlement",
    "webhook_ingest",
    "chat_stream_finalization",
];

const MUTATING_OPERATIONS: &[&str] = &[
    "control_plane_record",
    "tenant_bootstrap",
    "thread_turn",
    "run_create",
    "run_dispatch",
    "run_cancel",
    "run_retry",
    "workflow_child",
    "delegation_merge",
    "gateway_tool",
    "gateway_browser",
    "deployed_agent_action",
    "deployed_agent_runtime",
    "webhook_ingest",
    "chat_stream_finalization",
];

const OWNER_REQUIRED_OPERATIONS: &[&str] = &[
    "tenant_bootstrap",
    "billing_entitlement",
    "deployed_agent_action",
    "deployed_agent_runtime",
];

pub fn platform_orchestration_decision_command(payload: &Value) -> Value {
    let operation = normalize_operation(&text(payload, &["operation", "action"]));
    let tenant_id = fallback(text(payload, &["tenant_id"]), "default");
    let workspace_id = text(payload, &["workspace_id"]);
    let actor_id = text(payload, &["actor_id", "user_id", "owner_user_id"]);
    let actor_role = normalize(&text(payload, &["actor_role", "workspace_role", "role"]));
    let run_id = text(payload, &["run_id"]);
    let thread_id = text(payload, &["thread_id"]);
    let request_id = text(payload, &["request_id"]);
    let gateway_id = text(payload, &["gateway_id"]);
    let deployed_agent_id = text(payload, &["deployed_agent_id", "agent_id"]);
    let workflow_id = text(payload, &["workflow_id"]);
    let parent_run_id = text(payload, &["parent_run_id"]);
    let execution_target = normalize_target(&text(
        payload,
        &["execution_target", "runtime_target", "target"],
    ));
    let runtime_mode = fallback(
        normalize(&text(payload, &["runtime_mode"])),
        "hosted_secure",
    );
    let route_kind = fallback(
        normalize(&text(payload, &["route_kind", "record_type"])),
        "unknown",
    );
    let workflow_depth = number(payload, &["workflow_depth", "turn_depth"], 0);
    let max_workflow_depth = number(
        payload,
        &["max_workflow_depth", "workflow_turn_depth_limit"],
        30,
    )
    .max(1);
    let active_run_count = number(payload, &["active_run_count"], 0);
    let max_active_runs = number(payload, &["max_active_runs"], 32).max(1);
    let history_window_days = number(payload, &["history_window_days"], 30).max(1);
    let requested_history_days = number(payload, &["requested_history_days"], 1).max(0);
    let workspace_access = flag(
        payload,
        &["workspace_access", "workspace_access_allowed"],
        true,
    );
    let owner_access = flag(
        payload,
        &["owner_access", "owner_access_allowed"],
        owner_or_admin(&actor_role),
    );
    let entitlement_ok = flag(payload, &["entitlement_ok", "workspace_entitled"], true);
    let billing_ok = flag(payload, &["billing_ok", "budget_available"], true);
    let csrf_valid = flag(payload, &["csrf_valid"], true);
    let api_key_valid = flag(payload, &["api_key_valid", "authenticated"], true);
    let kill_switch_enabled = flag(payload, &["kill_switch_enabled"], false);
    let safe_mode_enabled = flag(payload, &["safe_mode_enabled"], false);
    let quota_ok = flag(payload, &["quota_ok", "gateway_quota_allowed"], true);
    let runtime_attached = flag(payload, &["runtime_attached"], execution_target == "cloud");
    let approval_provided = flag(
        payload,
        &["approval_provided", "owner_approval_provided"],
        false,
    );
    let webhook_signature_valid = flag(payload, &["webhook_signature_valid"], true);
    let route_payload_present = present(payload, &["payload", "body", "request"]);

    let mut blocks: Vec<String> = Vec::new();
    let mut approvals: Vec<String> = Vec::new();
    let mut normalized_flags: Vec<&str> = Vec::new();

    if operation.is_empty() {
        blocks.push("platform_orchestration_operation_missing".to_string());
    } else if !OPERATIONS.contains(&operation.as_str()) {
        blocks.push(format!(
            "unknown_platform_orchestration_operation:{operation}"
        ));
    }
    if workspace_id.is_empty() {
        blocks.push("workspace_id_missing".to_string());
    }
    if !workspace_access {
        blocks.push("workspace_access_denied".to_string());
    }
    if MUTATING_OPERATIONS.contains(&operation.as_str()) && actor_id.is_empty() {
        blocks.push("actor_id_missing_for_mutation".to_string());
    }
    if OWNER_REQUIRED_OPERATIONS.contains(&operation.as_str()) && !owner_access {
        blocks.push("owner_access_required".to_string());
    }
    if !csrf_valid && MUTATING_OPERATIONS.contains(&operation.as_str()) {
        blocks.push("csrf_validation_failed".to_string());
    }
    if !api_key_valid
        && matches!(
            operation.as_str(),
            "gateway_route" | "gateway_tool" | "gateway_browser" | "webhook_ingest"
        )
    {
        blocks.push("api_authentication_failed".to_string());
    }
    if kill_switch_enabled
        && !matches!(
            operation.as_str(),
            "run_cancel" | "gateway_route" | "chat_stream_finalization"
        )
    {
        blocks.push("kill_switch_blocks_orchestration".to_string());
    }
    if !entitlement_ok
        && matches!(
            operation.as_str(),
            "run_create"
                | "run_dispatch"
                | "gateway_tool"
                | "gateway_browser"
                | "deployed_agent_runtime"
        )
    {
        blocks.push("workspace_entitlement_missing".to_string());
    }
    if !billing_ok
        && matches!(
            operation.as_str(),
            "run_create" | "run_dispatch" | "deployed_agent_runtime"
        )
    {
        blocks.push("billing_or_budget_unavailable".to_string());
    }
    if operation == "control_plane_record" && route_kind == "unknown" {
        blocks.push("control_plane_record_type_missing".to_string());
    }
    if matches!(
        operation.as_str(),
        "run_create" | "run_dispatch" | "run_stream" | "run_cancel" | "run_retry"
    ) && run_id.is_empty()
        && operation != "run_create"
    {
        blocks.push("run_id_missing".to_string());
    }
    if matches!(
        operation.as_str(),
        "thread_turn" | "chat_stream_finalization"
    ) && thread_id.is_empty()
    {
        blocks.push("thread_id_missing".to_string());
    }
    if operation == "chat_stream_finalization" && request_id.is_empty() {
        blocks.push("request_id_missing".to_string());
    }
    if active_run_count >= max_active_runs
        && matches!(operation.as_str(), "run_create" | "run_dispatch")
    {
        blocks.push("workspace_run_concurrency_exhausted".to_string());
    }
    if requested_history_days > history_window_days
        && matches!(operation.as_str(), "run_stream" | "thread_turn")
    {
        blocks.push("history_window_exceeded".to_string());
    }
    if operation == "workflow_child" {
        if parent_run_id.is_empty() {
            blocks.push("parent_run_id_missing".to_string());
        }
        if workflow_id.is_empty() {
            blocks.push("workflow_id_missing".to_string());
        }
        if workflow_depth >= max_workflow_depth {
            blocks.push("workflow_recursion_depth_exceeded".to_string());
        }
    }
    if operation == "delegation_merge" && parent_run_id.is_empty() {
        blocks.push("delegation_parent_run_id_missing".to_string());
    }
    if matches!(
        operation.as_str(),
        "gateway_route" | "gateway_tool" | "gateway_browser"
    ) {
        if gateway_id.is_empty() {
            blocks.push("gateway_id_missing".to_string());
        }
        if !quota_ok {
            blocks.push("gateway_quota_exceeded".to_string());
        }
        if safe_mode_enabled && !approval_provided {
            approvals.push("safe_mode_gateway_action_requires_approval".to_string());
        }
    }
    if matches!(
        operation.as_str(),
        "deployed_agent_action" | "deployed_agent_runtime"
    ) {
        if deployed_agent_id.is_empty() {
            blocks.push("deployed_agent_id_missing".to_string());
        }
        if !runtime_attached && operation == "deployed_agent_runtime" {
            blocks.push("deployed_agent_runtime_not_attached".to_string());
        }
    }
    if operation == "webhook_ingest" {
        if !webhook_signature_valid {
            blocks.push("webhook_signature_invalid".to_string());
        }
        if !route_payload_present {
            blocks.push("webhook_payload_missing".to_string());
        }
    }
    if execution_target != "cloud" {
        normalized_flags.push("non_cloud_execution_target");
        if !runtime_attached {
            blocks.push("runtime_attachment_missing".to_string());
        }
    }
    if runtime_mode == "privileged_device" && !approval_provided {
        approvals.push("privileged_runtime_mode_requires_approval".to_string());
    }
    if safe_mode_enabled {
        normalized_flags.push("safe_mode_enabled");
    }
    if approval_provided {
        normalized_flags.push("approval_provided");
    }

    blocks.sort();
    blocks.dedup();
    approvals.sort();
    approvals.dedup();
    normalized_flags.sort();
    normalized_flags.dedup();

    let decision = if !blocks.is_empty() {
        "block"
    } else if !approvals.is_empty() && !approval_provided {
        "require_approval"
    } else {
        "allow"
    };
    let reason = if !blocks.is_empty() {
        blocks.join("; ")
    } else if decision == "require_approval" {
        approvals.join("; ")
    } else {
        "platform_orchestration_policy_satisfied".to_string()
    };

    json!({
        "ok": decision != "block",
        "decision": decision,
        "reason": reason,
        "operation": operation,
        "next_action": next_action(&operation, decision),
        "approval_required": decision == "require_approval",
        "cacheable": false,
        "audit_visibility": if decision == "allow" { "standard" } else { "security" },
        "scope": {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "actor_id": actor_id,
            "actor_role": actor_role,
            "workspace_access": workspace_access,
            "owner_access": owner_access,
            "entitlement_ok": entitlement_ok,
            "billing_ok": billing_ok,
        },
        "run": {
            "run_id": run_id,
            "thread_id": thread_id,
            "request_id": request_id,
            "active_run_count": active_run_count,
            "max_active_runs": max_active_runs,
            "history_window_days": history_window_days,
            "requested_history_days": requested_history_days,
        },
        "workflow": {
            "workflow_id": workflow_id,
            "parent_run_id": parent_run_id,
            "workflow_depth": workflow_depth,
            "max_workflow_depth": max_workflow_depth,
        },
        "runtime": {
            "execution_target": execution_target,
            "runtime_mode": runtime_mode,
            "runtime_attached": runtime_attached,
        },
        "gateway": {
            "gateway_id": gateway_id,
            "quota_ok": quota_ok,
        },
        "deployed_agent": {
            "deployed_agent_id": deployed_agent_id,
        },
        "normalized_flags": normalized_flags,
        "block_reasons": blocks,
        "approval_reasons": approvals,
    })
}

fn text(payload: &Value, keys: &[&str]) -> String {
    for key in keys {
        if let Some(Value::String(value)) = payload.get(*key) {
            let trimmed = value.trim();
            if !trimmed.is_empty() {
                return trimmed.to_string();
            }
        }
    }
    String::new()
}

fn number(payload: &Value, keys: &[&str], default: i64) -> i64 {
    for key in keys {
        if let Some(value) = payload.get(*key) {
            if let Some(number) = value.as_i64() {
                return number;
            }
            if let Some(text) = value.as_str() {
                if let Ok(parsed) = text.trim().parse::<i64>() {
                    return parsed;
                }
            }
        }
    }
    default
}

fn flag(payload: &Value, keys: &[&str], default: bool) -> bool {
    for key in keys {
        if let Some(value) = payload.get(*key) {
            if let Some(flag) = value.as_bool() {
                return flag;
            }
            if let Some(text) = value.as_str() {
                return matches!(
                    normalize(text).as_str(),
                    "1" | "true" | "yes" | "on" | "enabled" | "valid" | "allow"
                );
            }
            if let Some(number) = value.as_i64() {
                return number > 0;
            }
        }
    }
    default
}

fn present(payload: &Value, keys: &[&str]) -> bool {
    for key in keys {
        if let Some(value) = payload.get(*key) {
            match value {
                Value::Null => {}
                Value::String(text) => {
                    if !text.trim().is_empty() {
                        return true;
                    }
                }
                Value::Array(items) => {
                    if !items.is_empty() {
                        return true;
                    }
                }
                Value::Object(map) => {
                    if !map.is_empty() {
                        return true;
                    }
                }
                _ => return true,
            }
        }
    }
    false
}

fn normalize(value: &str) -> String {
    value
        .trim()
        .to_ascii_lowercase()
        .replace('-', "_")
        .replace(' ', "_")
}

fn fallback(value: String, fallback: &str) -> String {
    if value.is_empty() {
        fallback.to_string()
    } else {
        value
    }
}

fn normalize_target(value: &str) -> String {
    match normalize(value).as_str() {
        "local" | "local_companion" | "this_device" | "my_computer" => {
            "local_companion".to_string()
        }
        "cloud" | "hosted" | "cloud_default" => "cloud".to_string(),
        "cloud_computer" | "virtual_computer" => "cloud_computer".to_string(),
        "self_hosted" | "server" | "vps" => "self_hosted".to_string(),
        "" => "cloud".to_string(),
        other => other.to_string(),
    }
}

fn normalize_operation(value: &str) -> String {
    match normalize(value).as_str() {
        "record" | "control_plane_record" => "control_plane_record".to_string(),
        "workspace_access" | "access" => "workspace_access".to_string(),
        "tenant_bootstrap" | "bootstrap" => "tenant_bootstrap".to_string(),
        "thread_turn" | "turn" => "thread_turn".to_string(),
        "run_create" | "create_run" => "run_create".to_string(),
        "run_dispatch" | "dispatch_run" => "run_dispatch".to_string(),
        "run_stream" | "stream_run" => "run_stream".to_string(),
        "run_cancel" | "cancel_run" => "run_cancel".to_string(),
        "run_retry" | "retry_run" => "run_retry".to_string(),
        "workflow_child" | "child_run" => "workflow_child".to_string(),
        "delegation_merge" | "merge_delegation" => "delegation_merge".to_string(),
        "gateway_route" | "gateway" => "gateway_route".to_string(),
        "gateway_tool" | "tool" => "gateway_tool".to_string(),
        "gateway_browser" | "browser" => "gateway_browser".to_string(),
        "deployed_agent_action" | "agent_action" => "deployed_agent_action".to_string(),
        "deployed_agent_runtime" | "agent_runtime" => "deployed_agent_runtime".to_string(),
        "billing_entitlement" | "entitlement" => "billing_entitlement".to_string(),
        "webhook_ingest" | "webhook" => "webhook_ingest".to_string(),
        "chat_stream_finalization" | "chat_finalization" => "chat_stream_finalization".to_string(),
        "" => String::new(),
        other => other.to_string(),
    }
}

fn owner_or_admin(role: &str) -> bool {
    matches!(
        role,
        "owner" | "admin" | "workspace_owner" | "workspace_admin" | "super_admin"
    )
}

fn next_action(operation: &str, decision: &str) -> &'static str {
    if decision == "block" {
        return "stop_platform_orchestration";
    }
    if decision == "require_approval" {
        return "request_platform_orchestration_approval";
    }
    match operation {
        "control_plane_record" => "write_control_plane_record",
        "workspace_access" => "allow_workspace_access",
        "tenant_bootstrap" => "bootstrap_tenant_workspace",
        "thread_turn" => "record_thread_turn",
        "run_create" => "create_or_route_run",
        "run_dispatch" => "dispatch_run",
        "run_stream" => "stream_run_events",
        "run_cancel" => "cancel_run",
        "run_retry" => "retry_run",
        "workflow_child" => "create_workflow_child_run",
        "delegation_merge" => "merge_delegation_result",
        "gateway_route" => "route_gateway_request",
        "gateway_tool" => "execute_gateway_tool",
        "gateway_browser" => "execute_gateway_browser_action",
        "deployed_agent_action" => "execute_deployed_agent_action",
        "deployed_agent_runtime" => "bind_deployed_agent_runtime",
        "billing_entitlement" => "evaluate_billing_entitlement",
        "webhook_ingest" => "ingest_webhook_trigger",
        "chat_stream_finalization" => "finalize_chat_stream",
        _ => "review_platform_orchestration",
    }
}

#[cfg(test)]
mod platform_orchestration_kernel_boundary_tests {
    use super::platform_orchestration_decision_command;

    #[test]
    fn run_create_requires_actor_identity_for_mutation() {
        let decision = platform_orchestration_decision_command(&serde_json::json!({
            "operation": "run_create",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "workspace_access": true
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("block")
        );
        assert!(decision
            .get("reason")
            .and_then(|value| value.as_str())
            .unwrap_or("")
            .contains("actor_id_missing_for_mutation"));
    }

    #[test]
    fn run_create_allows_satisfied_policy() {
        let decision = platform_orchestration_decision_command(&serde_json::json!({
            "operation": "run_create",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "actor_id": "user-1",
            "actor_role": "member",
            "workspace_access": true,
            "entitlement_ok": true,
            "billing_ok": true,
            "runtime_attached": true,
            "execution_target": "cloud"
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("allow")
        );
        assert_eq!(
            decision.get("next_action").and_then(|value| value.as_str()),
            Some("create_or_route_run")
        );
    }
}

use serde_json::{json, Value};

const OPERATIONS: &[&str] = &[
    "create_draft",
    "update_config",
    "deploy",
    "pause",
    "kill",
    "recover",
    "archive",
    "recovery_action",
    "emergency_stop",
    "public_route",
    "telegram_readiness",
    "analytics_read",
    "admin_dashboard",
    "audit_export",
    "conversation_list",
    "conversation_detail",
    "memory_list",
    "activity_list",
    "external_user_delete",
    "knowledge_verify",
    "knowledge_upload",
    "business_insight_review",
    "business_insight_apply",
    "shop_evaluate",
    "test_turn",
    "runtime_session_kill",
];

const STATES: &[&str] = &[
    "draft",
    "private_test",
    "ready_for_review",
    "live",
    "paused",
    "suspended",
    "archived",
];

const PUBLIC_ROUTABLE_STATES: &[&str] = &["live", "paused", "suspended"];

pub fn deployed_agent_service_decision_command(payload: &Value) -> Value {
    let operation = normalize_operation(&text(payload, &["operation", "action"]));
    let tenant_id = fallback(text(payload, &["tenant_id"]), "default");
    let workspace_id = text(payload, &["workspace_id", "owner_workspace_id"]);
    let actor_id = text(payload, &["actor_id", "user_id", "owner_user_id"]);
    let actor_role = normalize(&text(payload, &["actor_role", "workspace_role", "role"]));
    let deployed_agent_id = text(payload, &["deployed_agent_id", "agent_id", "id"]);
    let state = fallback(
        normalize(&text(
            payload,
            &["deployment_state", "current_state", "state"],
        )),
        "draft",
    );
    let runtime_target = normalize_runtime_target(&text(
        payload,
        &["runtime_target", "studio_agent_mode", "mode"],
    ));
    let channel_key = normalize(&text(payload, &["channel_key", "channel"]));
    let session_id = text(payload, &["session_id", "runtime_session_id"]);
    let external_user_id = text(payload, &["external_user_id"]);
    let insight_id = text(payload, &["insight_id", "business_insight_id"]);
    let recovery_action = normalize(&text(payload, &["recovery_action"]));
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
    let budget_available = flag(payload, &["budget_available", "billing_ok"], true);
    let approval_provided = flag(
        payload,
        &["approval_provided", "owner_approval_provided"],
        false,
    );
    let privacy_contract_complete = flag(payload, &["privacy_contract_complete"], false);
    let privacy_contract_accepted = flag(payload, &["privacy_contract_accepted"], false);
    let computer_safety_complete = flag(payload, &["computer_safety_contract_complete"], false);
    let required_fields_complete = flag(
        payload,
        &["required_fields_complete", "basic_readiness_complete"],
        false,
    );
    let backing_install_present = flag(payload, &["backing_install_present"], false);
    let runtime_eligible = flag(payload, &["runtime_eligible"], true);
    let live_channel_configured = flag(
        payload,
        &[
            "live_channel_configured",
            "live_channel_configuration_present",
        ],
        false,
    );
    let public_endpoint_matches = flag(
        payload,
        &["public_endpoint_matches", "channel_endpoint_matches"],
        true,
    );
    let knowledge_reference_only = flag(payload, &["knowledge_reference_only"], true);
    let kill_switch_active = flag(payload, &["kill_switch_active"], state == "suspended");
    let retention_lock = flag(payload, &["retention_lock"], false);
    let audit_export_approval = flag(payload, &["audit_export_approval"], approval_provided);
    let privacy_delete_verified = flag(payload, &["privacy_delete_verified"], false);
    let active_run_count = number(payload, &["active_run_count"], 0);
    let max_active_runs = number(payload, &["max_active_runs"], 32).max(1);
    let monthly_cost_cap_usd_cents = number(payload, &["monthly_cost_cap_usd_cents"], 0);
    let max_monthly_cost_cap_usd_cents =
        number(payload, &["max_monthly_cost_cap_usd_cents"], 100_000);
    let daily_message_limit = number(payload, &["daily_message_limit"], 0);
    let max_daily_message_limit = number(payload, &["max_daily_message_limit"], 10_000);

    let mut blocks: Vec<String> = Vec::new();
    let mut approvals: Vec<String> = Vec::new();
    let mut flags: Vec<&str> = Vec::new();

    if operation.is_empty() {
        blocks.push("deployed_agent_service_operation_missing".to_string());
    } else if !OPERATIONS.contains(&operation.as_str()) {
        blocks.push(format!(
            "unknown_deployed_agent_service_operation:{operation}"
        ));
    }
    if workspace_id.is_empty() {
        blocks.push("workspace_id_missing".to_string());
    }
    if !workspace_access {
        blocks.push("workspace_access_denied".to_string());
    }
    if mutating_operation(&operation) && actor_id.is_empty() {
        blocks.push("actor_id_missing_for_mutation".to_string());
    }
    if owner_required(&operation) && !owner_access {
        blocks.push("deployed_agent_admin_access_required".to_string());
    }
    if !STATES.contains(&state.as_str()) {
        blocks.push("deployed_agent_state_unknown".to_string());
    }
    if requires_agent_id(&operation) && deployed_agent_id.is_empty() {
        blocks.push("deployed_agent_id_missing".to_string());
    }
    if state == "archived"
        && !matches!(
            operation.as_str(),
            "recover" | "analytics_read" | "activity_list" | "audit_export"
        )
    {
        blocks.push("deployed_agent_archived".to_string());
    }
    if !entitlement_ok
        && matches!(
            operation.as_str(),
            "create_draft" | "deploy" | "runtime_session_kill" | "test_turn" | "shop_evaluate"
        )
    {
        blocks.push("deployed_agent_entitlement_missing".to_string());
    }
    if !budget_available && matches!(operation.as_str(), "deploy" | "test_turn" | "shop_evaluate") {
        blocks.push("deployed_agent_budget_unavailable".to_string());
    }
    if operation == "deploy" {
        if !matches!(
            state.as_str(),
            "draft" | "private_test" | "ready_for_review" | "paused"
        ) {
            blocks.push("deployed_agent_state_not_deployable".to_string());
        }
        if !required_fields_complete {
            blocks.push("deployed_agent_required_fields_missing".to_string());
        }
        if !backing_install_present {
            blocks.push("backing_install_missing".to_string());
        }
        if !privacy_contract_complete {
            blocks.push("privacy_contract_incomplete".to_string());
        }
        if !privacy_contract_accepted {
            blocks.push("privacy_contract_not_accepted".to_string());
        }
        if computer_runtime_required(&runtime_target) && !computer_safety_complete {
            blocks.push("computer_safety_contract_incomplete".to_string());
        }
        if !runtime_eligible {
            blocks.push("runtime_not_eligible".to_string());
        }
        if !live_channel_configured {
            blocks.push("deployed_agent_live_channel_missing".to_string());
        }
    }
    if operation == "pause"
        && !matches!(state.as_str(), "live" | "private_test" | "ready_for_review")
    {
        blocks.push("deployed_agent_state_not_pausable".to_string());
    }
    if operation == "kill" && !approval_provided && actor_role != "system" {
        approvals.push("deployed_agent_kill_requires_approval".to_string());
    }
    if operation == "recover" {
        if !matches!(state.as_str(), "paused" | "suspended" | "archived") {
            blocks.push("deployed_agent_state_not_recoverable".to_string());
        }
        if kill_switch_active && !approval_provided {
            approvals.push("kill_switch_recovery_requires_approval".to_string());
        }
    }
    if operation == "archive" {
        if state == "live" && !approval_provided {
            approvals.push("live_deployed_agent_archive_requires_approval".to_string());
        }
        if retention_lock {
            blocks.push("retention_lock_blocks_archive".to_string());
        }
    }
    if operation == "recovery_action" {
        if !matches!(
            recovery_action.as_str(),
            "disable_channels"
                | "revoke_connector_access"
                | "clear_kill_switch"
                | "rebuild_runtime"
        ) {
            blocks.push("unsupported_recovery_action".to_string());
        }
    }
    if operation == "emergency_stop" && !approval_provided && actor_role != "system" {
        approvals.push("workspace_emergency_stop_requires_approval".to_string());
    }
    if operation == "public_route" {
        if !PUBLIC_ROUTABLE_STATES.contains(&state.as_str()) {
            blocks.push("deployed_agent_not_publicly_routable".to_string());
        }
        if !public_endpoint_matches {
            blocks.push("deployed_agent_channel_endpoint_mismatch".to_string());
        }
        if channel_key.is_empty() {
            blocks.push("channel_key_missing".to_string());
        }
    }
    if matches!(operation.as_str(), "audit_export" | "external_user_delete") {
        flags.push("sensitive_data_operation");
        if operation == "audit_export" && !audit_export_approval {
            approvals.push("audit_export_requires_approval".to_string());
        }
        if operation == "external_user_delete" {
            if external_user_id.is_empty() {
                blocks.push("external_user_id_missing".to_string());
            }
            if !privacy_delete_verified {
                blocks.push("external_user_privacy_delete_not_verified".to_string());
            }
        }
    }
    if operation == "conversation_detail" && session_id.is_empty() {
        blocks.push("conversation_session_id_missing".to_string());
    }
    if operation == "runtime_session_kill" {
        if session_id.is_empty() {
            blocks.push("runtime_session_id_missing".to_string());
        }
        if !approval_provided && actor_role != "system" {
            approvals.push("runtime_session_kill_requires_approval".to_string());
        }
    }
    if operation == "knowledge_upload" && !knowledge_reference_only {
        blocks.push("knowledge_sources_must_be_references_only".to_string());
    }
    if matches!(
        operation.as_str(),
        "business_insight_review" | "business_insight_apply"
    ) && insight_id.is_empty()
    {
        blocks.push("business_insight_id_missing".to_string());
    }
    if active_run_count >= max_active_runs
        && matches!(operation.as_str(), "deploy" | "test_turn" | "shop_evaluate")
    {
        blocks.push("deployed_agent_runtime_concurrency_exhausted".to_string());
    }
    if monthly_cost_cap_usd_cents > max_monthly_cost_cap_usd_cents
        && matches!(
            operation.as_str(),
            "create_draft" | "update_config" | "deploy"
        )
    {
        blocks.push("monthly_cost_cap_exceeds_plan".to_string());
    }
    if daily_message_limit > max_daily_message_limit
        && matches!(
            operation.as_str(),
            "create_draft" | "update_config" | "deploy"
        )
    {
        blocks.push("daily_message_limit_exceeds_plan".to_string());
    }
    if computer_runtime_required(&runtime_target) {
        flags.push("computer_runtime_required");
    }
    if kill_switch_active {
        flags.push("kill_switch_active");
    }
    if approval_provided {
        flags.push("approval_provided");
    }

    blocks.sort();
    blocks.dedup();
    approvals.sort();
    approvals.dedup();
    flags.sort();
    flags.dedup();

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
        "deployed_agent_service_policy_satisfied".to_string()
    };

    json!({
        "ok": decision != "block",
        "decision": decision,
        "reason": reason,
        "operation": operation,
        "next_action": next_action(&operation, decision),
        "mutation_plan": mutation_plan(payload, &operation, &state, decision),
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
            "budget_available": budget_available,
        },
        "deployed_agent": {
            "deployed_agent_id": deployed_agent_id,
            "state": state,
            "runtime_target": runtime_target,
            "channel_key": channel_key,
            "session_id": session_id,
            "external_user_id": external_user_id,
            "insight_id": insight_id,
        },
        "readiness": {
            "required_fields_complete": required_fields_complete,
            "backing_install_present": backing_install_present,
            "privacy_contract_complete": privacy_contract_complete,
            "privacy_contract_accepted": privacy_contract_accepted,
            "computer_safety_contract_complete": computer_safety_complete,
            "runtime_eligible": runtime_eligible,
            "live_channel_configured": live_channel_configured,
        },
        "limits": {
            "active_run_count": active_run_count,
            "max_active_runs": max_active_runs,
            "monthly_cost_cap_usd_cents": monthly_cost_cap_usd_cents,
            "max_monthly_cost_cap_usd_cents": max_monthly_cost_cap_usd_cents,
            "daily_message_limit": daily_message_limit,
            "max_daily_message_limit": max_daily_message_limit,
        },
        "normalized_flags": flags,
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

fn normalize_runtime_target(value: &str) -> String {
    match normalize(value).as_str() {
        "cloud" | "cloud_default" | "hosted_secure" | "text" | "text_agent" => "cloud".to_string(),
        "local" | "local_secure" | "local_companion" | "my_computer" => "my_computer".to_string(),
        "device" | "privileged_device" => "my_computer".to_string(),
        "cloud_computer" | "virtual_computer" => "cloud_computer".to_string(),
        "self_hosted" | "self_hosted_business" | "self_hosted_agent" => "self_hosted".to_string(),
        "" => "cloud".to_string(),
        other => other.to_string(),
    }
}

fn normalize_operation(value: &str) -> String {
    match normalize(value).as_str() {
        "create" | "create_draft" => "create_draft".to_string(),
        "update" | "update_config" => "update_config".to_string(),
        "deploy" => "deploy".to_string(),
        "pause" => "pause".to_string(),
        "kill" => "kill".to_string(),
        "recover" => "recover".to_string(),
        "archive" => "archive".to_string(),
        "recovery" | "recovery_action" => "recovery_action".to_string(),
        "emergency_stop" => "emergency_stop".to_string(),
        "public_route" | "route" => "public_route".to_string(),
        "telegram_readiness" => "telegram_readiness".to_string(),
        "analytics" | "analytics_read" => "analytics_read".to_string(),
        "admin_dashboard" => "admin_dashboard".to_string(),
        "audit_export" => "audit_export".to_string(),
        "conversation_list" => "conversation_list".to_string(),
        "conversation_detail" => "conversation_detail".to_string(),
        "memory" | "memory_list" => "memory_list".to_string(),
        "activity" | "activity_list" => "activity_list".to_string(),
        "external_user_delete" | "privacy_delete" => "external_user_delete".to_string(),
        "knowledge_verify" => "knowledge_verify".to_string(),
        "knowledge_upload" => "knowledge_upload".to_string(),
        "business_insight_review" => "business_insight_review".to_string(),
        "business_insight_apply" => "business_insight_apply".to_string(),
        "shop_evaluate" => "shop_evaluate".to_string(),
        "test_turn" => "test_turn".to_string(),
        "runtime_session_kill" => "runtime_session_kill".to_string(),
        "" => String::new(),
        other => other.to_string(),
    }
}

fn mutating_operation(operation: &str) -> bool {
    matches!(
        operation,
        "create_draft"
            | "update_config"
            | "deploy"
            | "pause"
            | "kill"
            | "recover"
            | "archive"
            | "recovery_action"
            | "emergency_stop"
            | "external_user_delete"
            | "knowledge_upload"
            | "business_insight_review"
            | "business_insight_apply"
            | "test_turn"
            | "runtime_session_kill"
    )
}

fn owner_required(operation: &str) -> bool {
    !matches!(operation, "public_route" | "shop_evaluate" | "test_turn")
}

fn requires_agent_id(operation: &str) -> bool {
    !matches!(
        operation,
        "" | "create_draft" | "emergency_stop" | "telegram_readiness"
    )
}

fn owner_or_admin(role: &str) -> bool {
    matches!(
        role,
        "owner" | "admin" | "workspace_owner" | "workspace_admin" | "super_admin" | "system"
    )
}

fn computer_runtime_required(runtime_target: &str) -> bool {
    matches!(
        runtime_target,
        "cloud_computer" | "my_computer" | "self_hosted"
    )
}

fn next_action(operation: &str, decision: &str) -> &'static str {
    if decision == "block" {
        return "stop_deployed_agent_service_operation";
    }
    if decision == "require_approval" {
        return "request_deployed_agent_service_approval";
    }
    match operation {
        "create_draft" => "create_deployed_agent_draft",
        "update_config" => "update_deployed_agent_config",
        "deploy" => "deploy_deployed_agent",
        "pause" => "pause_deployed_agent",
        "kill" => "kill_deployed_agent",
        "recover" => "recover_deployed_agent",
        "archive" => "archive_deployed_agent",
        "recovery_action" => "apply_deployed_agent_recovery_action",
        "emergency_stop" => "emergency_stop_workspace_deployed_agents",
        "public_route" => "route_public_deployed_agent",
        "telegram_readiness" => "read_telegram_readiness",
        "analytics_read" => "read_deployed_agent_analytics",
        "admin_dashboard" => "read_deployed_agent_admin_dashboard",
        "audit_export" => "export_deployed_agent_audit_logs",
        "conversation_list" => "list_deployed_agent_conversations",
        "conversation_detail" => "read_deployed_agent_conversation_detail",
        "memory_list" => "list_deployed_agent_memory",
        "activity_list" => "list_deployed_agent_activity",
        "external_user_delete" => "delete_deployed_agent_external_user_data",
        "knowledge_verify" => "verify_deployed_agent_knowledge",
        "knowledge_upload" => "upload_deployed_agent_knowledge_reference",
        "business_insight_review" => "review_deployed_agent_business_insight",
        "business_insight_apply" => "apply_deployed_agent_business_insight",
        "shop_evaluate" => "evaluate_shop_assistant",
        "test_turn" => "execute_deployed_agent_test_turn",
        "runtime_session_kill" => "kill_deployed_agent_runtime_session",
        _ => "review_deployed_agent_service_operation",
    }
}

fn mutation_plan(payload: &Value, operation: &str, state: &str, decision: &str) -> Value {
    if decision != "allow" {
        return json!({});
    }
    match operation {
        "pause" => json!({
            "target_state": "paused",
            "set_last_paused_at": true,
        }),
        "kill" => json!({
            "target_state": "suspended",
            "stop_active_runs": true,
            "activate_kill_switch": true,
            "set_last_paused_at": true,
        }),
        "recover" => json!({
            "target_state": if matches!(state, "paused" | "suspended" | "archived") {
                "ready_for_review"
            } else {
                state
            },
            "clear_kill_switch": true,
        }),
        "archive" => json!({
            "target_state": "archived",
            "stop_active_runs": true,
            "disable_channels": true,
        }),
        "emergency_stop" => json!({
            "target_state": "suspended",
            "stop_active_runs": true,
            "activate_workspace_emergency_stop": true,
            "set_last_paused_at": true,
            "stop_reason": "workspace_emergency_stop",
        }),
        "runtime_session_kill" => {
            let binding = normalize(&text(payload, &["runtime_session_binding", "binding"]));
            json!({
                "terminate_cloud_session": binding == "cloud_computer_agent",
                "terminate_self_hosted_session": binding == "self_hosted_agent",
                "terminate_session_record": true,
            })
        }
        _ => json!({}),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn kill_decision_emits_runtime_stop_and_kill_switch_plan() {
        let response = deployed_agent_service_decision_command(&json!({
            "operation": "kill",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "deployed_agent_id": "dagent_1",
            "deployment_state": "live",
            "actor_id": "user-1",
            "actor_role": "admin",
            "approval_provided": true,
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["mutation_plan"]["target_state"], "suspended");
        assert_eq!(response["mutation_plan"]["stop_active_runs"], true);
        assert_eq!(response["mutation_plan"]["activate_kill_switch"], true);
    }

    #[test]
    fn archive_decision_emits_disable_channels_plan() {
        let response = deployed_agent_service_decision_command(&json!({
            "operation": "archive",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "deployed_agent_id": "dagent_1",
            "deployment_state": "paused",
            "actor_id": "user-1",
            "actor_role": "admin",
            "approval_provided": true,
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["mutation_plan"]["target_state"], "archived");
        assert_eq!(response["mutation_plan"]["disable_channels"], true);
    }

    #[test]
    fn emergency_stop_decision_emits_workspace_stop_plan() {
        let response = deployed_agent_service_decision_command(&json!({
            "operation": "emergency_stop",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "deployed_agent_id": "dagent_1",
            "deployment_state": "live",
            "actor_id": "system",
            "actor_role": "system",
            "approval_provided": true,
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["mutation_plan"]["target_state"], "suspended");
        assert_eq!(response["mutation_plan"]["stop_active_runs"], true);
        assert_eq!(
            response["mutation_plan"]["activate_workspace_emergency_stop"],
            true
        );
        assert_eq!(response["mutation_plan"]["set_last_paused_at"], true);
        assert_eq!(
            response["mutation_plan"]["stop_reason"],
            "workspace_emergency_stop"
        );
    }

    #[test]
    fn runtime_session_kill_emits_cloud_termination_plan() {
        let response = deployed_agent_service_decision_command(&json!({
            "operation": "runtime_session_kill",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "deployed_agent_id": "dagent_1",
            "deployment_state": "live",
            "actor_id": "user-1",
            "actor_role": "admin",
            "approval_provided": true,
            "session_id": "sess-1",
            "runtime_session_binding": "cloud_computer_agent",
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["mutation_plan"]["terminate_cloud_session"], true);
        assert_eq!(
            response["mutation_plan"]["terminate_self_hosted_session"],
            false
        );
        assert_eq!(response["mutation_plan"]["terminate_session_record"], true);
    }
}

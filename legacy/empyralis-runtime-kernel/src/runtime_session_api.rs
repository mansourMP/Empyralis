use serde_json::{json, Value};

const OPERATIONS: &[&str] = &[
    "runtime_register",
    "runtime_bootstrap",
    "runtime_heartbeat",
    "runtime_start",
    "runtime_stop",
    "runtime_recover",
    "runtime_revoke",
    "runtime_control_stream",
    "run_hard_kill",
    "runtime_task_claim",
    "runtime_task_heartbeat",
    "runtime_task_control_state",
    "runtime_task_complete",
    "runtime_task_pause",
    "runtime_task_fail",
    "self_hosted_node_enroll",
    "self_hosted_node_heartbeat",
    "self_hosted_command_enqueue",
    "self_hosted_command_claim",
    "self_hosted_command_result",
    "hardware_action_execute",
    "hardware_action_stop",
    "machine_enrollment_state",
    "machine_bootstrap_complete",
    "machine_control",
    "tts_request",
    "stt_request",
];

const TERMINAL_RUN_STATUSES: &[&str] = &[
    "succeeded",
    "failed",
    "cancelled",
    "canceled",
    "terminated",
    "expired",
];

pub fn runtime_session_api_decision_command(payload: &Value) -> Value {
    let operation = normalize_operation(&text(payload, &["operation", "action", "route_action"]));
    let workspace_id = text(payload, &["workspace_id"]);
    let tenant_id = fallback(normalize(&text(payload, &["tenant_id"])), "default");
    let runtime_id = text(payload, &["runtime_id", "machine_id", "worker_id"]);
    let runtime_type = fallback(normalize(&text(payload, &["runtime_type"])), "local");
    let runtime_role = fallback(normalize(&text(payload, &["runtime_role"])), "generic");
    let instance_id = text(payload, &["instance_id"]);
    let run_id = text(payload, &["run_id", "current_run_id"]);
    let node_id = text(payload, &["runtime_node_id", "node_id"]);
    let agent_id = text(payload, &["agent_id", "deployed_agent_id"]);
    let action_id = text(payload, &["action_id", "hardware_action_id"]);
    let command_type = fallback(
        normalize(&text(payload, &["command_type"])),
        "runtime_action",
    );
    let status = fallback(
        normalize(&text(payload, &["status", "run_status"])),
        "queued",
    );
    let target = normalize_target(&text(
        payload,
        &["runtime_target", "execution_target", "target"],
    ));
    let content_type = text(payload, &["content_type", "audio_content_type"]).to_ascii_lowercase();
    let operator_role = normalize(&text(payload, &["operator_role", "workspace_role"]));
    let session_token_present = present(payload, &["session_token", "node_session_token"]);
    let enrollment_token_present = present(payload, &["enrollment_token"]);
    let public_key_present = present(payload, &["public_key"]);
    let command_payload_present = present(payload, &["command_payload", "payload", "arguments"]);
    let api_key_valid = flag(payload, &["api_key_valid", "authenticated"], true);
    let workspace_access = flag(
        payload,
        &["workspace_access", "workspace_access_allowed"],
        true,
    );
    let entitlement_ok = flag(
        payload,
        &["entitlement_ok", "advanced_features_enabled"],
        true,
    );
    let approvals_enabled = flag(payload, &["approvals_enabled"], true);
    let local_runtime_enabled = flag(
        payload,
        &["local_runtime_enabled", "local_companion_enabled"],
        true,
    );
    let runtime_registered = flag(payload, &["runtime_registered", "machine_registered"], true);
    let runtime_session_valid = flag(
        payload,
        &["runtime_session_valid", "session_valid"],
        session_token_present,
    );
    let kill_switch_enabled = flag(payload, &["kill_switch_enabled"], false);
    let operator_approved = flag(payload, &["operator_approved", "approval_provided"], false);
    let current_run_bound = flag(payload, &["current_run_bound"], !run_id.is_empty());
    let max_commands = number(payload, &["max_commands"], 1);
    let lease_seconds = number(payload, &["lease_seconds", "ttl_seconds"], 120);
    let timeout_seconds = number(payload, &["timeout_seconds"], 30);
    let text_length = number(payload, &["text_length", "text_len"], 0);
    let audio_bytes = number(payload, &["audio_bytes", "content_length"], 0);
    let max_concurrent_sessions = number(payload, &["max_concurrent_sessions"], 1);
    let capability_count = list_len(payload, &["capabilities", "required_capabilities"]);
    let artifact_count = list_len(payload, &["artifacts"]);

    let mut blocks: Vec<String> = Vec::new();
    let mut approvals: Vec<String> = Vec::new();
    let mut flags: Vec<&str> = Vec::new();

    if operation.is_empty() {
        blocks.push("runtime_api_operation_missing".to_string());
    } else if !OPERATIONS.contains(&operation.as_str()) {
        blocks.push(format!("unknown_runtime_api_operation:{operation}"));
    }
    if !api_key_valid {
        blocks.push("runtime_api_authentication_failed".to_string());
    }
    if requires_workspace(&operation) && workspace_id.is_empty() {
        blocks.push("workspace_id_missing".to_string());
    }
    if requires_workspace(&operation) && !workspace_access {
        blocks.push("workspace_access_denied".to_string());
    }
    if requires_runtime_id(&operation) && runtime_id.is_empty() {
        blocks.push("runtime_id_missing".to_string());
    }
    if requires_session_token(&operation) {
        flags.push("runtime_session_required");
        if !session_token_present {
            blocks.push("runtime_session_token_missing".to_string());
        } else if !runtime_session_valid {
            blocks.push("runtime_session_invalid".to_string());
        }
        if !instance_id.is_empty() {
            flags.push("runtime_instance_bound");
        }
    }
    if requires_enrollment_token(&operation) {
        flags.push("enrollment_token_required");
        if !enrollment_token_present {
            blocks.push("enrollment_token_missing".to_string());
        }
    }
    if matches!(
        operation.as_str(),
        "runtime_register"
            | "runtime_bootstrap"
            | "runtime_heartbeat"
            | "runtime_start"
            | "runtime_stop"
            | "runtime_recover"
            | "runtime_task_claim"
    ) && !local_runtime_enabled
    {
        blocks.push("local_runtime_disabled".to_string());
    }
    if requires_existing_runtime(&operation) && !runtime_registered {
        blocks.push("runtime_not_registered".to_string());
    }
    if kill_switch_enabled
        && !matches!(
            operation.as_str(),
            "hardware_action_stop"
                | "runtime_task_fail"
                | "runtime_task_pause"
                | "runtime_heartbeat"
                | "runtime_stop"
                | "runtime_revoke"
                | "run_hard_kill"
                | "machine_control"
        )
    {
        blocks.push("kill_switch_blocks_runtime_api".to_string());
    }
    if matches!(operation.as_str(), "runtime_register" | "runtime_bootstrap") {
        if matches!(runtime_type.as_str(), "local" | "local_companion") {
            flags.push("local_companion_registration");
        }
        if runtime_role == "generic" && capability_count == 0 {
            approvals.push("generic_runtime_without_capabilities_requires_review".to_string());
        }
    }
    if operation == "self_hosted_node_enroll" {
        if !public_key_present {
            blocks.push("self_hosted_public_key_missing".to_string());
        }
        if max_concurrent_sessions > 64 {
            blocks.push("self_hosted_concurrency_exceeds_hard_limit".to_string());
        } else if max_concurrent_sessions > 16 {
            approvals.push("self_hosted_high_concurrency_requires_approval".to_string());
        }
    }
    if matches!(
        operation.as_str(),
        "self_hosted_node_heartbeat" | "self_hosted_command_claim" | "self_hosted_command_result"
    ) && node_id.is_empty()
        && runtime_id.is_empty()
    {
        blocks.push("self_hosted_node_identity_missing".to_string());
    }
    if operation == "self_hosted_command_enqueue" {
        if node_id.is_empty() {
            blocks.push("runtime_node_id_missing".to_string());
        }
        if agent_id.is_empty() {
            blocks.push("agent_id_missing".to_string());
        }
        if !command_payload_present {
            blocks.push("command_payload_missing".to_string());
        }
        if !entitlement_ok {
            blocks.push("self_hosted_runtime_entitlement_missing".to_string());
        }
    }
    if operation == "self_hosted_command_claim" {
        if max_commands <= 0 || max_commands > 50 {
            blocks.push("max_commands_out_of_range".to_string());
        }
        if !(15..=600).contains(&lease_seconds) {
            blocks.push("command_claim_lease_out_of_range".to_string());
        }
    }
    if matches!(
        operation.as_str(),
        "runtime_task_claim"
            | "runtime_task_heartbeat"
            | "runtime_task_control_state"
            | "runtime_task_complete"
            | "runtime_task_pause"
            | "runtime_task_fail"
    ) {
        if TERMINAL_RUN_STATUSES.contains(&status.as_str())
            && !matches!(
                operation.as_str(),
                "runtime_task_complete" | "runtime_task_fail"
            )
        {
            blocks.push("terminal_run_cannot_accept_runtime_task_event".to_string());
        }
        if operation == "runtime_task_claim" && capability_count == 0 {
            flags.push("claim_without_required_capabilities");
        }
    }
    if matches!(
        operation.as_str(),
        "runtime_task_complete" | "runtime_task_pause" | "runtime_task_fail"
    ) && !current_run_bound
    {
        blocks.push("runtime_task_not_bound_to_run".to_string());
    }
    if matches!(
        operation.as_str(),
        "hardware_action_execute" | "hardware_action_stop"
    ) {
        if operation == "hardware_action_execute" && action_id.is_empty() {
            blocks.push("hardware_action_id_missing".to_string());
        }
        if operation == "hardware_action_stop" && run_id.is_empty() {
            blocks.push("run_id_missing".to_string());
        }
        if !entitlement_ok {
            blocks.push("hardware_action_entitlement_missing".to_string());
        }
        if !owner_or_admin(&operator_role) && !operator_approved {
            approvals.push("hardware_action_requires_operator_approval".to_string());
        }
        if target == "cloud_default" {
            flags.push("cloud_default_runtime_target");
        } else {
            flags.push("hardware_runtime_session_required");
            if !session_token_present && runtime_id.is_empty() && node_id.is_empty() {
                approvals.push("hardware_target_requires_runtime_session".to_string());
            }
        }
    }
    if matches!(
        operation.as_str(),
        "machine_enrollment_state" | "machine_bootstrap_complete"
    ) && !enrollment_token_present
    {
        blocks.push("machine_enrollment_token_missing".to_string());
    }
    if operation == "machine_control" && !owner_or_admin(&operator_role) && !operator_approved {
        approvals.push("machine_control_requires_owner_or_admin".to_string());
    }
    if operation == "run_hard_kill" {
        if run_id.is_empty() {
            blocks.push("run_id_missing".to_string());
        }
        if !owner_or_admin(&operator_role) && !operator_approved {
            approvals.push("run_hard_kill_requires_owner_or_admin".to_string());
        }
    }
    if operation == "tts_request" {
        if text_length <= 0 {
            blocks.push("tts_text_missing".to_string());
        } else if text_length > 12_000 {
            blocks.push("tts_text_exceeds_hard_limit".to_string());
        } else if text_length > 4_096 {
            approvals.push("tts_text_exceeds_default_limit".to_string());
        }
    }
    if operation == "stt_request" {
        if content_type.is_empty() {
            blocks.push("stt_content_type_missing".to_string());
        } else if !supported_stt_content_type(&content_type) {
            blocks.push("unsupported_stt_content_type".to_string());
        }
        if audio_bytes <= 0 {
            blocks.push("stt_audio_missing".to_string());
        } else if audio_bytes > 128 * 1024 * 1024 {
            blocks.push("stt_audio_exceeds_hard_limit".to_string());
        } else if audio_bytes > 32 * 1024 * 1024 {
            approvals.push("stt_audio_exceeds_default_limit".to_string());
        }
    }
    if !approvals_enabled && !approvals.is_empty() {
        blocks.push("approval_required_but_approvals_disabled".to_string());
    }
    if timeout_seconds <= 0 {
        blocks.push("timeout_must_be_positive".to_string());
    } else if timeout_seconds > 120
        && matches!(
            operation.as_str(),
            "hardware_action_execute" | "hardware_action_stop"
        )
    {
        blocks.push("hardware_action_timeout_exceeds_limit".to_string());
    } else if timeout_seconds > 600 {
        approvals.push("runtime_api_timeout_exceeds_default_limit".to_string());
    }
    if artifact_count > 25
        && matches!(
            operation.as_str(),
            "runtime_task_complete" | "self_hosted_command_result"
        )
    {
        approvals.push("large_artifact_batch_requires_review".to_string());
    }

    blocks.sort();
    blocks.dedup();
    approvals.sort();
    approvals.dedup();
    flags.sort();
    flags.dedup();

    let decision = if !blocks.is_empty() {
        "block"
    } else if !approvals.is_empty() && !operator_approved {
        "require_approval"
    } else {
        "allow"
    };
    let reason = if !blocks.is_empty() {
        blocks.join("; ")
    } else if decision == "require_approval" {
        approvals.join("; ")
    } else {
        "runtime_session_api_policy_satisfied".to_string()
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
            "workspace_access": workspace_access,
            "entitlement_ok": entitlement_ok,
        },
        "runtime": {
            "runtime_id": runtime_id,
            "runtime_type": runtime_type,
            "runtime_role": runtime_role,
            "instance_id": instance_id,
            "registered": runtime_registered,
            "session_token_present": session_token_present,
            "session_valid": runtime_session_valid,
            "capability_count": capability_count,
        },
        "run": {
            "run_id": run_id,
            "status": status,
            "current_run_bound": current_run_bound,
        },
        "self_hosted": {
            "node_id": node_id,
            "agent_id": agent_id,
            "command_type": command_type,
            "max_commands": max_commands,
            "lease_seconds": lease_seconds,
            "max_concurrent_sessions": max_concurrent_sessions,
        },
        "hardware_action": {
            "action_id": action_id,
            "runtime_target": target,
            "operator_role": operator_role,
            "timeout_seconds": timeout_seconds,
        },
        "media": {
            "content_type": content_type,
            "text_length": text_length,
            "audio_bytes": audio_bytes,
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

fn flag(payload: &Value, keys: &[&str], default: bool) -> bool {
    for key in keys {
        if let Some(value) = payload.get(*key) {
            if let Some(flag) = value.as_bool() {
                return flag;
            }
            if let Some(text) = value.as_str() {
                return matches!(
                    normalize(text).as_str(),
                    "1" | "true" | "yes" | "on" | "enabled" | "valid"
                );
            }
            if let Some(number) = value.as_i64() {
                return number > 0;
            }
        }
    }
    default
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

fn list_len(payload: &Value, keys: &[&str]) -> i64 {
    for key in keys {
        if let Some(Value::Array(items)) = payload.get(*key) {
            return items.len() as i64;
        }
    }
    0
}

fn normalize(value: &str) -> String {
    value.trim().to_ascii_lowercase().replace(['-', ' '], "_")
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
        "cloud" | "cloud_default" | "hosted" => "cloud_default".to_string(),
        "local" | "local_companion" | "this_device" | "user_device_gateway" => {
            "user_device_gateway".to_string()
        }
        "self_hosted" | "server" | "vps" => "self_hosted_node".to_string(),
        "" => "cloud_default".to_string(),
        other => other.to_string(),
    }
}

fn normalize_operation(value: &str) -> String {
    match normalize(value).as_str() {
        "register" | "runtime_register" | "runtime_registration" => "runtime_register".to_string(),
        "bootstrap" | "companion_bootstrap" | "runtime_bootstrap" => {
            "runtime_bootstrap".to_string()
        }
        "heartbeat" | "runtime_heartbeat" => "runtime_heartbeat".to_string(),
        "start" | "runtime_start" | "start_runtime" => "runtime_start".to_string(),
        "stop" | "runtime_stop" | "stop_runtime" => "runtime_stop".to_string(),
        "recover" | "runtime_recover" | "recover_runtime" => "runtime_recover".to_string(),
        "revoke" | "runtime_revoke" | "revoke_runtime" => "runtime_revoke".to_string(),
        "control_stream" | "runtime_control_stream" | "runtime_stream" => {
            "runtime_control_stream".to_string()
        }
        "run_hard_kill" | "hard_kill_run" | "kill_run" => "run_hard_kill".to_string(),
        "claim" | "task_claim" | "runtime_task_claim" => "runtime_task_claim".to_string(),
        "task_heartbeat" | "runtime_task_heartbeat" => "runtime_task_heartbeat".to_string(),
        "control_state" | "runtime_task_control_state" => "runtime_task_control_state".to_string(),
        "complete" | "task_complete" | "runtime_task_complete" => {
            "runtime_task_complete".to_string()
        }
        "pause" | "task_pause" | "runtime_task_pause" => "runtime_task_pause".to_string(),
        "fail" | "task_fail" | "runtime_task_fail" => "runtime_task_fail".to_string(),
        "self_hosted_enroll" | "self_hosted_node_enroll" => "self_hosted_node_enroll".to_string(),
        "self_hosted_heartbeat" | "self_hosted_node_heartbeat" => {
            "self_hosted_node_heartbeat".to_string()
        }
        "self_hosted_enqueue" | "self_hosted_command_enqueue" => {
            "self_hosted_command_enqueue".to_string()
        }
        "self_hosted_claim" | "self_hosted_command_claim" => {
            "self_hosted_command_claim".to_string()
        }
        "self_hosted_result" | "self_hosted_command_result" => {
            "self_hosted_command_result".to_string()
        }
        "hardware_execute" | "hardware_action_execute" => "hardware_action_execute".to_string(),
        "hardware_stop" | "hardware_action_stop" => "hardware_action_stop".to_string(),
        "machine_enrollment_state" | "enrollment_state" => "machine_enrollment_state".to_string(),
        "machine_bootstrap_complete" | "bootstrap_complete" => {
            "machine_bootstrap_complete".to_string()
        }
        "machine_control" | "control_machine" => "machine_control".to_string(),
        "tts" | "tts_request" => "tts_request".to_string(),
        "stt" | "transcribe" | "stt_request" => "stt_request".to_string(),
        "" => String::new(),
        other => other.to_string(),
    }
}

fn requires_workspace(operation: &str) -> bool {
    !matches!(
        operation,
        "" | "runtime_heartbeat"
            | "runtime_task_heartbeat"
            | "runtime_task_control_state"
            | "runtime_task_complete"
            | "runtime_task_pause"
            | "runtime_task_fail"
            | "self_hosted_node_heartbeat"
            | "self_hosted_command_claim"
            | "self_hosted_command_result"
    )
}

fn requires_runtime_id(operation: &str) -> bool {
    matches!(
        operation,
        "runtime_heartbeat"
            | "runtime_task_claim"
            | "runtime_start"
            | "runtime_stop"
            | "runtime_recover"
            | "runtime_revoke"
            | "runtime_control_stream"
            | "runtime_task_heartbeat"
            | "runtime_task_control_state"
            | "runtime_task_complete"
            | "runtime_task_pause"
            | "runtime_task_fail"
    )
}

fn requires_session_token(operation: &str) -> bool {
    matches!(
        operation,
        "runtime_heartbeat"
            | "runtime_task_claim"
            | "runtime_start"
            | "runtime_stop"
            | "runtime_recover"
            | "runtime_control_stream"
            | "runtime_task_heartbeat"
            | "runtime_task_control_state"
            | "runtime_task_complete"
            | "runtime_task_pause"
            | "runtime_task_fail"
            | "self_hosted_node_heartbeat"
            | "self_hosted_command_claim"
            | "self_hosted_command_result"
    )
}

fn requires_enrollment_token(operation: &str) -> bool {
    matches!(
        operation,
        "runtime_bootstrap"
            | "self_hosted_node_enroll"
            | "machine_enrollment_state"
            | "machine_bootstrap_complete"
    )
}

fn requires_existing_runtime(operation: &str) -> bool {
    matches!(
        operation,
        "runtime_heartbeat"
            | "runtime_task_claim"
            | "runtime_start"
            | "runtime_stop"
            | "runtime_recover"
            | "runtime_revoke"
            | "runtime_control_stream"
            | "runtime_task_heartbeat"
            | "runtime_task_control_state"
            | "runtime_task_complete"
            | "runtime_task_pause"
            | "runtime_task_fail"
    )
}

fn owner_or_admin(role: &str) -> bool {
    matches!(
        role,
        "owner" | "admin" | "workspace_owner" | "workspace_admin" | "super_admin"
    )
}

fn supported_stt_content_type(value: &str) -> bool {
    value.starts_with("audio/")
        || matches!(
            value,
            "application/octet_stream" | "application/octet-stream" | "video/mp4" | "video/webm"
        )
}

fn next_action(operation: &str, decision: &str) -> &'static str {
    if decision == "block" {
        return "stop_runtime_api_request";
    }
    if decision == "require_approval" {
        return "request_runtime_api_approval";
    }
    match operation {
        "runtime_register" => "register_runtime",
        "runtime_bootstrap" => "bootstrap_runtime_companion",
        "runtime_heartbeat" => "touch_runtime_session",
        "runtime_start" => "start_runtime_session",
        "runtime_stop" => "stop_runtime_session",
        "runtime_recover" => "recover_runtime_session",
        "runtime_revoke" => "revoke_runtime_session",
        "runtime_control_stream" => "stream_runtime_control",
        "run_hard_kill" => "hard_kill_runtime_run",
        "runtime_task_claim" => "claim_runtime_task",
        "runtime_task_heartbeat" => "record_runtime_task_heartbeat",
        "runtime_task_control_state" => "read_runtime_task_control_state",
        "runtime_task_complete" => "complete_runtime_task",
        "runtime_task_pause" => "pause_runtime_task",
        "runtime_task_fail" => "fail_runtime_task",
        "self_hosted_node_enroll" => "enroll_self_hosted_node",
        "self_hosted_node_heartbeat" => "touch_self_hosted_node",
        "self_hosted_command_enqueue" => "enqueue_self_hosted_command",
        "self_hosted_command_claim" => "claim_self_hosted_command",
        "self_hosted_command_result" => "record_self_hosted_command_result",
        "hardware_action_execute" => "execute_hardware_action",
        "hardware_action_stop" => "stop_hardware_action",
        "machine_enrollment_state" => "record_machine_enrollment_state",
        "machine_bootstrap_complete" => "complete_machine_bootstrap",
        "machine_control" => "apply_machine_control",
        "tts_request" => "synthesize_tts",
        "stt_request" => "transcribe_audio",
        _ => "review_runtime_api_request",
    }
}

#[cfg(test)]
mod runtime_session_api_extension_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn runtime_control_stream_requires_valid_runtime_session() {
        let decision = runtime_session_api_decision_command(&json!({
            "operation": "runtime_control_stream",
            "workspace_id": "default",
            "runtime_id": "worker-1",
            "session_token": "sess",
            "runtime_session_valid": true
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["next_action"], "stream_runtime_control");
        assert_eq!(decision["runtime"]["runtime_id"], "worker-1");
    }

    #[test]
    fn runtime_control_stream_blocks_missing_session_token() {
        let decision = runtime_session_api_decision_command(&json!({
            "operation": "runtime_control_stream",
            "workspace_id": "default",
            "runtime_id": "worker-1"
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "runtime_session_token_missing");
    }

    #[test]
    fn stt_request_blocks_unsupported_media_type() {
        let decision = runtime_session_api_decision_command(&json!({
            "operation": "stt_request",
            "workspace_id": "default",
            "content_type": "text/plain",
            "audio_bytes": 10
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "unsupported_stt_content_type");
    }

    #[test]
    fn tts_request_allows_normal_sized_text() {
        let decision = runtime_session_api_decision_command(&json!({
            "operation": "tts_request",
            "workspace_id": "default",
            "text_length": 512
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["next_action"], "synthesize_tts");
    }
}

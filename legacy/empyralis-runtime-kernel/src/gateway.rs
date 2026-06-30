use crate::policy::normalize_token;
use crate::protocol;
use serde_json::{json, Value};

const PRIVILEGED_TOOL_MARKERS: &[&str] = &[
    "shell",
    "terminal",
    "filesystem.write",
    "file.write",
    "browser",
    "computer",
    "secret",
    "credential",
    "external",
    "email.send",
    "calendar.write",
];

pub fn gateway_protocol_decision_command(input: &Value) -> Value {
    let message_type = normalize_message_type(
        protocol::string_field(input, "message_type")
            .or_else(|| protocol::string_field(input, "method"))
            .or_else(|| protocol::string_field(input, "operation"))
            .as_deref()
            .unwrap_or(""),
    );
    if message_type.is_empty() {
        return protocol::block("gateway_message_type_missing");
    }

    let session_id = protocol::string_field(input, "session_id");
    let workspace_id = protocol::string_field(input, "workspace_id");
    let agent_id = protocol::string_field(input, "agent_id")
        .or_else(|| protocol::string_field(input, "agent_install_id"));
    let tool_name = protocol::string_field(input, "tool_name")
        .or_else(|| protocol::string_field(input, "tool"));
    let payload_present = input.get("payload").is_some()
        || protocol::string_field(input, "message").is_some()
        || protocol::string_field(input, "prompt").is_some();
    let authenticated = boolish_default(input, "authenticated", true);
    let protocol_version = protocol::string_field(input, "protocol_version")
        .unwrap_or_else(|| "phase6-rust-kernel-v1".to_string());

    if !authenticated && message_type != "health_check" {
        return gateway_response(
            "block",
            "gateway_unauthenticated",
            &message_type,
            "reject",
            session_id,
            workspace_id,
            agent_id,
            tool_name,
            &protocol_version,
            false,
        );
    }

    match message_type.as_str() {
        "health_check" => gateway_response(
            "allow",
            "gateway_health_check_allowed",
            &message_type,
            "health",
            session_id,
            workspace_id,
            agent_id,
            tool_name,
            &protocol_version,
            false,
        ),
        "session_create" => {
            if workspace_id.is_none() {
                return protocol::block("workspace_id_missing");
            }
            gateway_response(
                "allow",
                "gateway_session_create_allowed",
                &message_type,
                "create_session",
                session_id,
                workspace_id,
                agent_id,
                tool_name,
                &protocol_version,
                false,
            )
        }
        "session_close" => {
            if session_id.is_none() {
                return protocol::block("session_id_missing");
            }
            gateway_response(
                "allow",
                "gateway_session_close_allowed",
                &message_type,
                "close_session",
                session_id,
                workspace_id,
                agent_id,
                tool_name,
                &protocol_version,
                false,
            )
        }
        "agent_turn" => {
            if session_id.is_none() {
                return protocol::block("session_id_missing");
            }
            if !payload_present {
                return protocol::block("agent_turn_payload_missing");
            }
            gateway_response(
                "allow",
                "gateway_agent_turn_allowed",
                &message_type,
                "route_to_agent",
                session_id,
                workspace_id,
                agent_id,
                tool_name,
                &protocol_version,
                false,
            )
        }
        "tool_result" => {
            if session_id.is_none() {
                return protocol::block("session_id_missing");
            }
            gateway_response(
                "allow",
                "gateway_tool_result_allowed",
                &message_type,
                "attach_tool_result",
                session_id,
                workspace_id,
                agent_id,
                tool_name,
                &protocol_version,
                false,
            )
        }
        "tool_invoke" => {
            if session_id.is_none() {
                return protocol::block("session_id_missing");
            }
            if !payload_present {
                return protocol::block("tool_invoke_payload_missing");
            }
            gateway_response(
                "allow",
                "gateway_tool_invoke_allowed",
                &message_type,
                "dispatch_tool_invoke",
                session_id,
                workspace_id,
                agent_id,
                tool_name,
                &protocol_version,
                false,
            )
        }
        "tool_interrupt" => {
            if session_id.is_none() {
                return protocol::block("session_id_missing");
            }
            if !payload_present {
                return protocol::block("tool_interrupt_payload_missing");
            }
            gateway_response(
                "allow",
                "gateway_tool_interrupt_allowed",
                &message_type,
                "dispatch_tool_interrupt",
                session_id,
                workspace_id,
                agent_id,
                tool_name,
                &protocol_version,
                false,
            )
        }
        "channel_outbound" => {
            if session_id.is_none() {
                return protocol::block("session_id_missing");
            }
            if !payload_present {
                return protocol::block("channel_outbound_payload_missing");
            }
            gateway_response(
                "allow",
                "gateway_channel_outbound_allowed",
                &message_type,
                "dispatch_channel_outbound",
                session_id,
                workspace_id,
                agent_id,
                tool_name,
                &protocol_version,
                false,
            )
        }
        "tool_use" => {
            if session_id.is_none() {
                return protocol::block("session_id_missing");
            }
            let Some(tool_name_value) = tool_name.clone() else {
                return protocol::block("tool_name_missing");
            };
            if privileged_tool(&tool_name_value) {
                return gateway_response(
                    "require_approval",
                    "gateway_privileged_tool_requires_approval",
                    &message_type,
                    "request_owner_approval",
                    session_id,
                    workspace_id,
                    agent_id,
                    Some(tool_name_value),
                    &protocol_version,
                    true,
                );
            }
            gateway_response(
                "allow",
                "gateway_tool_use_allowed",
                &message_type,
                "route_tool_use",
                session_id,
                workspace_id,
                agent_id,
                Some(tool_name_value),
                &protocol_version,
                false,
            )
        }
        _ => protocol::block("unknown_gateway_message_type"),
    }
}

fn gateway_response(
    decision: &str,
    reason: &str,
    message_type: &str,
    next_action: &str,
    session_id: Option<String>,
    workspace_id: Option<String>,
    agent_id: Option<String>,
    tool_name: Option<String>,
    protocol_version: &str,
    approval_required: bool,
) -> Value {
    json!({
        "ok": decision != "block",
        "decision": decision,
        "reason": reason,
        "message_type": message_type,
        "next_action": next_action,
        "session_id": session_id,
        "workspace_id": workspace_id,
        "agent_id": agent_id,
        "tool_name": tool_name,
        "protocol_version": protocol_version,
        "approval_required": approval_required || decision == "require_approval",
        "cacheable": message_type == "health_check",
        "audit_visibility": if decision == "allow" && message_type == "health_check" { "standard" } else { "security" },
    })
}

fn normalize_message_type(value: &str) -> String {
    match normalize_token(value).as_str() {
        "health_check" | "health" | "ping" => "health_check".to_string(),
        "session_create" | "create_session" | "session_new" => "session_create".to_string(),
        "session_close" | "close_session" | "session_end" => "session_close".to_string(),
        "agent_turn" | "turn" | "run_turn" => "agent_turn".to_string(),
        "tool_invoke" | "invoke_tool" => "tool_invoke".to_string(),
        "tool_interrupt" | "interrupt_tool" => "tool_interrupt".to_string(),
        "channel_outbound" | "outbound_channel" => "channel_outbound".to_string(),
        "tool_use" | "tool_call" | "use_tool" => "tool_use".to_string(),
        "tool_result" | "tool_response" | "result" => "tool_result".to_string(),
        _ => String::new(),
    }
}

fn privileged_tool(value: &str) -> bool {
    let token = value.trim().to_ascii_lowercase();
    PRIVILEGED_TOOL_MARKERS.iter().any(|marker| {
        token == *marker || token.starts_with(&format!("{marker}.")) || token.contains(marker)
    })
}

fn boolish_default(input: &Value, key: &str, default_value: bool) -> bool {
    if input.get(key).is_none() {
        return default_value;
    }
    match input.get(key) {
        Some(Value::Bool(value)) => *value,
        Some(Value::String(value)) => matches!(
            normalize_token(value).as_str(),
            "1" | "true" | "yes" | "on" | "enabled" | "active"
        ),
        Some(Value::Number(value)) => value.as_u64().unwrap_or(0) > 0,
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_message_type_blocks() {
        let response = gateway_protocol_decision_command(&json!({}));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "gateway_message_type_missing");
    }

    #[test]
    fn health_check_allows_without_auth() {
        let response = gateway_protocol_decision_command(&json!({
            "message_type": "health.check",
            "authenticated": false
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_action"], "health");
    }

    #[test]
    fn agent_turn_requires_session_and_payload() {
        let response = gateway_protocol_decision_command(&json!({
            "message_type": "agent.turn",
            "session_id": "s1"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "agent_turn_payload_missing");
    }

    #[test]
    fn privileged_tool_requires_approval() {
        let response = gateway_protocol_decision_command(&json!({
            "message_type": "tool.use",
            "session_id": "s1",
            "tool_name": "shell.execute"
        }));
        assert_eq!(response["decision"], "require_approval");
        assert_eq!(response["approval_required"], true);
    }

    #[test]
    fn normal_tool_use_allows() {
        let response = gateway_protocol_decision_command(&json!({
            "message_type": "tool.use",
            "session_id": "s1",
            "tool_name": "calculator"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_action"], "route_tool_use");
    }

    #[test]
    fn tool_invoke_allows_live_outbound_message() {
        let response = gateway_protocol_decision_command(&json!({
            "message_type": "tool.invoke",
            "session_id": "s1",
            "workspace_id": "w1",
            "tool_name": "computer_control.click",
            "payload": {"arguments": {"x": 1, "y": 2}}
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_action"], "dispatch_tool_invoke");
    }

    #[test]
    fn tool_interrupt_requires_payload() {
        let response = gateway_protocol_decision_command(&json!({
            "message_type": "tool.interrupt",
            "session_id": "s1"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "tool_interrupt_payload_missing");
    }

    #[test]
    fn channel_outbound_allows_live_outbound_message() {
        let response = gateway_protocol_decision_command(&json!({
            "message_type": "channel.outbound",
            "session_id": "s1",
            "workspace_id": "w1",
            "tool_name": "whatsapp_personal.send",
            "payload": {"text": "hello"}
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_action"], "dispatch_channel_outbound");
    }
}

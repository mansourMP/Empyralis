use crate::authorization;
use crate::execution_plan;
use serde_json::{json, Map, Value};

pub fn authorize_execution_command(input: &Value) -> Value {
    let authorization_input = authorization_payload(input);
    let authorization_output = authorization::authorize_request_command(&authorization_input);
    let authorization_decision = decision_of(&authorization_output);

    if authorization_decision == "block" {
        return json!({
            "ok": false,
            "decision": "block",
            "reason": reason_of(&authorization_output, "authorization_blocked"),
            "next_action": "deny_tool_execution",
            "authorization": authorization_output,
            "execution_plan": null,
            "approval_required": false,
            "cacheable": false,
            "audit_visibility": "security",
        });
    }

    let execution_output = execution_plan::execution_plan_command(input);
    let execution_decision = decision_of(&execution_output);
    if execution_decision == "block" {
        return json!({
            "ok": false,
            "decision": "block",
            "reason": reason_of(&execution_output, "execution_plan_blocked"),
            "next_action": "deny_tool_execution",
            "authorization": authorization_output,
            "execution_plan": execution_output,
            "approval_required": false,
            "cacheable": false,
            "audit_visibility": "security",
        });
    }

    let approval_required =
        authorization_decision == "require_approval" || execution_decision == "require_approval";
    let final_decision = if approval_required {
        "require_approval"
    } else {
        "allow"
    };

    json!({
        "ok": true,
        "decision": final_decision,
        "reason": if approval_required { "execution_requires_approval" } else { "execution_authorized" },
        "next_action": if approval_required {
            "request_tool_execution_approval"
        } else {
            "allow_tool_execution"
        },
        "authorization": authorization_output,
        "execution_plan": execution_output,
        "approval_required": approval_required,
        "cacheable": false,
        "audit_visibility": if approval_required { "security" } else { "standard" },
    })
}

fn authorization_payload(input: &Value) -> Value {
    let mut payload = input.clone();
    let Some(object) = payload.as_object_mut() else {
        let mut object = Map::new();
        object.insert(
            "capability".to_string(),
            Value::String("shell_command".to_string()),
        );
        object.insert(
            "action_class".to_string(),
            Value::String("execute".to_string()),
        );
        return Value::Object(object);
    };
    object
        .entry("capability".to_string())
        .or_insert_with(|| Value::String("shell_command".to_string()));
    object
        .entry("action_class".to_string())
        .or_insert_with(|| Value::String("execute".to_string()));
    if !object.contains_key("target_summary") {
        if let Some(command) = object.get("command").and_then(Value::as_str) {
            object.insert(
                "target_summary".to_string(),
                Value::String(command.to_string()),
            );
        }
    }
    payload
}

fn decision_of(value: &Value) -> String {
    value
        .get("decision")
        .and_then(Value::as_str)
        .unwrap_or("block")
        .to_string()
}

fn reason_of(value: &Value, fallback: &str) -> String {
    value
        .get("reason")
        .and_then(Value::as_str)
        .unwrap_or(fallback)
        .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_policy_blocks_before_execution_plan() {
        let response = authorize_execution_command(&json!({
            "command": "python3 script.py"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "policy_missing");
        assert_eq!(response["next_action"], "deny_tool_execution");
        assert_eq!(response["execution_plan"], Value::Null);
    }

    #[test]
    fn destructive_command_blocks_even_with_yolo_policy() {
        let response = authorize_execution_command(&json!({
            "policy": {"policy_id": "p1", "autonomy_mode": "yolo"},
            "command": "rm -rf /",
            "capability": "shell_command"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "destructive_command_blocked");
        assert_eq!(response["next_action"], "deny_tool_execution");
        assert_eq!(response["execution_plan"]["decision"], "block");
    }

    #[test]
    fn local_command_allows_with_yolo_policy() {
        let response = authorize_execution_command(&json!({
            "policy": {"policy_id": "p1", "autonomy_mode": "yolo"},
            "command": "python3 script.py",
            "capability": "shell_command",
            "action_class": "execute"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["approval_required"], false);
        assert_eq!(response["next_action"], "allow_tool_execution");
        assert_eq!(
            response["execution_plan"]["execution"]["sandbox_type"],
            "docker"
        );
    }

    #[test]
    fn network_command_requires_approval() {
        let response = authorize_execution_command(&json!({
            "policy": {"policy_id": "p1", "autonomy_mode": "yolo"},
            "command": "curl https://example.com",
            "capability": "shell_command",
            "action_class": "execute"
        }));
        assert_eq!(response["decision"], "require_approval");
        assert_eq!(response["approval_required"], true);
        assert_eq!(response["next_action"], "request_tool_execution_approval");
    }

    #[test]
    fn kill_switch_blocks_before_execution_plan() {
        let response = authorize_execution_command(&json!({
            "policy": {"policy_id": "p1", "autonomy_mode": "yolo"},
            "command": "python3 script.py",
            "capability": "shell_command",
            "kill_switch_enabled": true
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "kill_switch_enabled");
        assert_eq!(response["next_action"], "deny_tool_execution");
        assert_eq!(response["execution_plan"], Value::Null);
    }
}

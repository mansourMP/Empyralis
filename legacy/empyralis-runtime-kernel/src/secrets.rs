use crate::policy::normalize_token;
use crate::protocol;
use serde_json::{json, Value};

const SENSITIVE_NAME_MARKERS: &[&str] = &[
    "api_key",
    "apikey",
    "authorization",
    "auth_token",
    "bearer",
    "client_secret",
    "cookie",
    "credential",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "session_token",
    "token",
];

const SENSITIVE_VALUE_MARKERS: &[&str] = &[
    "bearer ",
    "api-key ",
    "api_key",
    "sk-",
    "sk-ant-",
    "ghp_",
    "gho_",
    "xoxb-",
    "-----begin private key-----",
];

pub fn inspect_secret_reference_command(input: &Value) -> Value {
    let name = protocol::string_field(input, "name")
        .or_else(|| protocol::string_field(input, "key"))
        .or_else(|| protocol::string_field(input, "secret_name"));
    let value = protocol::string_field(input, "value")
        .or_else(|| protocol::string_field(input, "secret_value"));
    if name.is_none() && value.is_none() {
        return json!({
            "ok": false,
            "decision": "block",
            "reason": "secret_reference_missing",
            "next_action": "deny_secret_resolution",
            "approval_required": false,
            "high_risk_credential": false,
            "secret_like": false,
            "risk_level": "low",
            "action_class": "classify",
            "name": Value::Null,
            "redacted_value": Value::Null,
            "matched_markers": [],
        });
    }

    let action_class = normalize_action_class(
        protocol::string_field(input, "action_class")
            .or_else(|| protocol::string_field(input, "operation"))
            .as_deref()
            .unwrap_or("classify"),
    );
    let mut markers = Vec::new();
    let name_secret = name
        .as_deref()
        .map(|item| collect_name_markers(item, &mut markers))
        .unwrap_or(false);
    let value_secret = value
        .as_deref()
        .map(|item| collect_value_markers(item, &mut markers))
        .unwrap_or(false);
    markers.sort();
    markers.dedup();

    let high_risk_credential = high_risk_credential(input);
    let secret_like = name_secret || value_secret;
    if !secret_like && !high_risk_credential {
        return json!({
            "ok": true,
            "decision": "allow",
            "reason": "no_secret_reference_detected",
            "next_action": "allow_secret_resolution",
            "secret_like": false,
            "risk_level": "low",
            "action_class": action_class,
            "name": name,
            "redacted_value": value,
            "matched_markers": [],
        });
    }

    let approval_id_present = protocol::string_field(input, "approval_id")
        .map(|value| !value.trim().is_empty())
        .unwrap_or(false);
    let (decision, reason, risk_level) = match action_class.as_str() {
        "classify" | "metadata" => ("allow", "secret_metadata_inspection_allowed", "medium"),
        "read" | "resolve" | "use" if high_risk_credential && approval_id_present => {
            ("allow", "secret_access_approved", "high")
        }
        "read" | "resolve" | "use" if high_risk_credential => (
            "require_approval",
            "secret_access_requires_approval",
            "high",
        ),
        "read" | "resolve" | "use" => ("allow", "secret_access_allowed_by_policy", "medium"),
        "expose" | "export" | "log" | "print" | "send" | "network" | "write" => {
            ("block", "secret_exfiltration_blocked", "critical")
        }
        _ => (
            "require_approval",
            "unknown_secret_action_requires_approval",
            "high",
        ),
    };

    json!({
        "ok": decision != "block",
        "decision": decision,
        "reason": reason,
        "next_action": match decision {
            "allow" => "allow_secret_resolution",
            "require_approval" => "request_secret_access_approval",
            _ => "deny_secret_resolution",
        },
        "approval_required": decision == "require_approval",
        "high_risk_credential": high_risk_credential,
        "secret_like": secret_like || high_risk_credential,
        "risk_level": risk_level,
        "action_class": action_class,
        "name": name,
        "redacted_value": value.as_deref().map(redact_secret_value),
        "matched_markers": markers,
    })
}

fn high_risk_credential(input: &Value) -> bool {
    if let Some(Value::Bool(value)) = input.get("approval_required") {
        return *value;
    }
    let mut tokens = Vec::new();
    for key in [
        "provider_id",
        "connector_id",
        "credential_id",
        "connector_class",
    ] {
        if let Some(value) = protocol::string_field(input, key) {
            tokens.push(value);
        }
    }
    flatten_risk_tokens(
        input.get("metadata").unwrap_or(&Value::Null),
        &mut tokens,
        128,
    );
    flatten_risk_tokens(
        input.get("risk_tags").unwrap_or(&Value::Null),
        &mut tokens,
        128,
    );
    let joined = tokens.join(" ").to_ascii_lowercase();
    [
        "admin",
        "administrator",
        "root",
        "privileged",
        "financial",
        "finance",
        "billing",
        "payment",
        "payout",
    ]
    .iter()
    .any(|marker| joined.contains(marker))
}

fn flatten_risk_tokens(value: &Value, tokens: &mut Vec<String>, budget: usize) {
    if tokens.len() >= budget {
        return;
    }
    match value {
        Value::String(value) => tokens.push(value.clone()),
        Value::Array(items) => {
            for item in items {
                flatten_risk_tokens(item, tokens, budget);
            }
        }
        Value::Object(object) => {
            for (key, value) in object {
                tokens.push(key.clone());
                flatten_risk_tokens(value, tokens, budget);
            }
        }
        Value::Bool(value) => tokens.push(value.to_string()),
        Value::Number(value) => tokens.push(value.to_string()),
        Value::Null => {}
    }
}

fn normalize_action_class(value: &str) -> String {
    match normalize_token(value).as_str() {
        "classify" | "inspect" | "metadata" => "classify".to_string(),
        "read" | "resolve" | "get" => "read".to_string(),
        "use" | "inject" => "use".to_string(),
        "expose" | "reveal" | "show" => "expose".to_string(),
        "export" | "copy" => "export".to_string(),
        "log" | "print" => "log".to_string(),
        "send" | "network" | "http" => "send".to_string(),
        "write" | "store" => "write".to_string(),
        other => other.to_string(),
    }
}

fn collect_name_markers(value: &str, markers: &mut Vec<String>) -> bool {
    let normalized = normalize_token(value);
    let mut matched = false;
    for marker in SENSITIVE_NAME_MARKERS {
        if normalized == *marker || normalized.contains(marker) {
            markers.push(format!("name:{marker}"));
            matched = true;
        }
    }
    matched
}

fn collect_value_markers(value: &str, markers: &mut Vec<String>) -> bool {
    let normalized = value.to_ascii_lowercase();
    let mut matched = false;
    for marker in SENSITIVE_VALUE_MARKERS {
        if normalized.contains(marker) {
            markers.push(format!("value:{marker}"));
            matched = true;
        }
    }
    matched
}

fn redact_secret_value(value: &str) -> String {
    if value.len() <= 8 {
        "***REDACTED***".to_string()
    } else {
        let prefix = value.chars().take(4).collect::<String>();
        format!("{prefix}...REDACTED")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_secret_reference_blocks() {
        let response = inspect_secret_reference_command(&json!({}));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "secret_reference_missing");
        assert_eq!(response["next_action"], "deny_secret_resolution");
    }

    #[test]
    fn secret_metadata_inspection_allows_without_raw_value() {
        let response = inspect_secret_reference_command(&json!({
            "name": "OPENAI_API_KEY",
            "value": "sk-test-secret-value",
            "action_class": "metadata"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["secret_like"], true);
        assert_eq!(response["redacted_value"], "sk-t...REDACTED");
        assert_eq!(response["next_action"], "allow_secret_resolution");
    }

    #[test]
    fn secret_read_requires_approval() {
        let response = inspect_secret_reference_command(&json!({
            "name": "refresh_token",
            "action_class": "read",
            "metadata": {"sensitivity": "financial_admin"}
        }));
        assert_eq!(response["decision"], "require_approval");
        assert_eq!(response["risk_level"], "high");
        assert_eq!(response["next_action"], "request_secret_access_approval");
    }

    #[test]
    fn secret_export_blocks() {
        let response = inspect_secret_reference_command(&json!({
            "name": "client_secret",
            "value": "Bearer sk-test-secret-value",
            "action_class": "export"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "secret_exfiltration_blocked");
        assert_eq!(response["risk_level"], "critical");
        assert_eq!(response["next_action"], "deny_secret_resolution");
    }

    #[test]
    fn non_secret_reference_allows() {
        let response = inspect_secret_reference_command(&json!({
            "name": "theme",
            "value": "light",
            "action_class": "read"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["secret_like"], false);
        assert_eq!(response["next_action"], "allow_secret_resolution");
    }
}

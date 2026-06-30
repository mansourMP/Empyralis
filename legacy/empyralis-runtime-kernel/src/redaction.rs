use serde_json::{json, Map, Value};

const SENSITIVE_KEYS: &[&str] = &[
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "bearer",
    "client_secret",
    "cookie",
    "credential",
    "key",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "session",
    "token",
];

const SECRET_MARKERS: &[&str] = &[
    "bearer ", "api-key ", "api_key", "sk-", "sk-ant-", "ghp_", "gho_", "xoxb-",
];

pub fn redact_diagnostics_command(input: &Value) -> Value {
    let data = input.get("data").unwrap_or(input);
    json!({
        "ok": true,
        "decision": "allow",
        "reason": "diagnostics_redacted",
        "next_action": "return_redacted_diagnostics",
        "redacted": redact_value(data, None),
    })
}

fn redact_value(value: &Value, key: Option<&str>) -> Value {
    if key.map(is_sensitive_key).unwrap_or(false) {
        return Value::String("***REDACTED***".to_string());
    }

    match value {
        Value::Object(object) => {
            let mut redacted = Map::new();
            for (child_key, child_value) in object {
                redacted.insert(
                    child_key.clone(),
                    redact_value(child_value, Some(child_key)),
                );
            }
            Value::Object(redacted)
        }
        Value::Array(items) => {
            Value::Array(items.iter().map(|item| redact_value(item, None)).collect())
        }
        Value::String(value) => Value::String(redact_string(value)),
        other => other.clone(),
    }
}

fn is_sensitive_key(key: &str) -> bool {
    let normalized = key.trim().to_ascii_lowercase().replace('-', "_");
    SENSITIVE_KEYS
        .iter()
        .any(|sensitive_key| normalized == *sensitive_key || normalized.contains(sensitive_key))
}

fn redact_string(value: &str) -> String {
    let lower = value.to_ascii_lowercase();
    if SECRET_MARKERS.iter().any(|marker| lower.contains(marker)) {
        if value.len() <= 12 {
            "***REDACTED***".to_string()
        } else {
            let prefix = value.chars().take(12).collect::<String>();
            format!("{prefix}...REDACTED")
        }
    } else {
        value.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redacts_sensitive_keys() {
        let response = redact_diagnostics_command(&json!({
            "data": {
                "token": "abc",
                "nested": {"api_key": "secret"},
                "normal": "visible"
            }
        }));
        assert_eq!(response["redacted"]["token"], "***REDACTED***");
        assert_eq!(response["redacted"]["nested"]["api_key"], "***REDACTED***");
        assert_eq!(response["redacted"]["normal"], "visible");
        assert_eq!(response["next_action"], "return_redacted_diagnostics");
    }

    #[test]
    fn redacts_secret_like_strings() {
        let response = redact_diagnostics_command(&json!({
            "data": {"message": "Bearer sk-test-secret-value"}
        }));
        assert_eq!(response["redacted"]["message"], "Bearer sk-te...REDACTED");
        assert_eq!(response["next_action"], "return_redacted_diagnostics");
    }
}

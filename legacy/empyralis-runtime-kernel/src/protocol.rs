use serde_json::{json, Value};

pub fn string_field(input: &Value, key: &str) -> Option<String> {
    input
        .get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned)
}

pub fn string_vec_field(input: &Value, key: &str) -> Vec<String> {
    input
        .get(key)
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(ToOwned::to_owned)
                .collect()
        })
        .unwrap_or_default()
}

pub fn policy_payload(input: &Value) -> Option<&Value> {
    input
        .get("policy")
        .or_else(|| if input.is_object() { Some(input) } else { None })
}

pub fn object_has_any_key(input: &Value, keys: &[&str]) -> bool {
    input
        .as_object()
        .map(|object| keys.iter().any(|key| object.contains_key(*key)))
        .unwrap_or(false)
}

pub fn block(reason: &str) -> Value {
    json!({
        "ok": false,
        "decision": "block",
        "reason": reason,
    })
}

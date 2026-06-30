use serde_json::{json, Value};

const MAX_GATEWAY_FRAME_BYTES: u64 = 16 * 1024 * 1024;
const MAX_GATEWAY_JSON_DEPTH: u64 = 32;
const SUPPORTED_PROTOCOL_VERSION: &str = "v1alpha2";

pub fn gateway_frame_decision_command(payload: &Value) -> Value {
    let kind = normalize_token(&text_any(payload, &["kind", "frame_kind"]));
    let message_type = text_any(payload, &["type", "message_type"]);
    let operation = normalize_operation(&text_any(payload, &["operation", "action"]));
    let frame_id = text_any(payload, &["id", "frame_id", "request_id"]);

    if !boolish(payload, "frame_object", true) {
        return block("gateway_frame_not_object", &kind, &message_type, 4408);
    }
    let size_bytes = numeric_any(
        payload,
        &["raw_size_bytes", "frame_size_bytes", "payload_size_bytes"],
    )
    .unwrap_or(0);
    if size_bytes > MAX_GATEWAY_FRAME_BYTES {
        return block("gateway_frame_too_large", &kind, &message_type, 4409);
    }
    let depth = numeric_any(payload, &["json_depth", "depth"]).unwrap_or(0);
    if depth > MAX_GATEWAY_JSON_DEPTH {
        return block("gateway_frame_too_deep", &kind, &message_type, 4408);
    }
    if kind.is_empty() {
        return block("gateway_frame_kind_missing", &kind, &message_type, 4408);
    }
    if !matches!(kind.as_str(), "request" | "response" | "event") {
        return block("gateway_frame_kind_invalid", &kind, &message_type, 4408);
    }
    if kind != "response" && message_type.trim().is_empty() {
        return block("gateway_frame_type_missing", &kind, &message_type, 4408);
    }

    if boolish(payload, "duplicate_frame_id", false) {
        return allow(
            "gateway_frame_duplicate_replay_allowed",
            &operation,
            "replay_cached_frame_response",
            &kind,
            &message_type,
            &frame_id,
            true,
            0,
        );
    }

    if (kind == "request" || kind == "response") && frame_id.is_empty() {
        return block("gateway_frame_id_missing", &kind, &message_type, 4408);
    }

    let seq = numeric_any(payload, &["seq", "sequence"]);
    let ack = numeric_any(payload, &["ack"]);
    let previous_seq = numeric_any(payload, &["previous_seq", "last_seq"]);
    let previous_ack = numeric_any(payload, &["previous_ack", "last_ack"]);
    if let (Some(next), Some(previous)) = (seq, previous_seq) {
        if next < previous {
            return block("gateway_frame_seq_regressed", &kind, &message_type, 4408);
        }
    }
    if let (Some(next), Some(previous)) = (ack, previous_ack) {
        if next < previous {
            return block("gateway_frame_ack_regressed", &kind, &message_type, 4408);
        }
    }

    if boolish(payload, "first_frame", false) {
        if kind != "request" || message_type != "gateway.connect" {
            return block("gateway_connect_required", &kind, &message_type, 4408);
        }
        let client_protocol_version = text_any(
            payload,
            &[
                "client_protocol_version",
                "protocol_version",
                "protocolVersion",
            ],
        );
        if client_protocol_version.is_empty() {
            return block(
                "gateway_protocol_version_required",
                &kind,
                &message_type,
                4408,
            );
        }
        if client_protocol_version != SUPPORTED_PROTOCOL_VERSION {
            return block(
                "gateway_protocol_version_unsupported",
                &kind,
                &message_type,
                4408,
            );
        }
    }

    if boolish(payload, "scope_required", false)
        && !boolish(payload, "scope_matches_registration", true)
    {
        return block("gateway_scope_mismatch", &kind, &message_type, 4403);
    }

    let next_action = match (kind.as_str(), message_type.as_str()) {
        ("request", "gateway.connect") => "accept_gateway_connect",
        ("request", _) => "route_gateway_request",
        ("response", _) => "resolve_gateway_response",
        ("event", _) => "handle_gateway_event",
        _ => "record_gateway_frame",
    };
    allow(
        "gateway_frame_allowed",
        &operation,
        next_action,
        &kind,
        &message_type,
        &frame_id,
        false,
        0,
    )
    .with_extra("seq", seq.map_or(Value::Null, |value| json!(value)))
    .with_extra("ack", ack.map_or(Value::Null, |value| json!(value)))
}

fn allow(
    reason: &str,
    operation: &str,
    next_action: &str,
    kind: &str,
    message_type: &str,
    frame_id: &str,
    cacheable: bool,
    close_code: u64,
) -> Value {
    json!({
        "ok": true,
        "decision": "allow",
        "reason": reason,
        "operation": if operation.is_empty() { "validate_frame" } else { operation },
        "next_action": next_action,
        "frame_kind": kind,
        "message_type": message_type,
        "frame_id": frame_id,
        "close_code": close_code,
        "approval_required": false,
        "cacheable": cacheable,
        "audit_visibility": "standard",
    })
}

fn block(reason: &str, kind: &str, message_type: &str, close_code: u64) -> Value {
    json!({
        "ok": false,
        "decision": "block",
        "reason": reason,
        "operation": "validate_frame",
        "next_action": "close_gateway_socket",
        "frame_kind": kind,
        "message_type": message_type,
        "close_code": close_code,
        "approval_required": false,
        "cacheable": false,
        "audit_visibility": "security",
    })
}

trait JsonExtra {
    fn with_extra(self, key: &str, value: Value) -> Value;
}

impl JsonExtra for Value {
    fn with_extra(self, key: &str, value: Value) -> Value {
        match self {
            Value::Object(mut map) => {
                map.insert(key.to_string(), value);
                Value::Object(map)
            }
            other => other,
        }
    }
}

fn normalize_operation(raw: &str) -> String {
    match normalize_token(raw).as_str() {
        "parse" | "parse_frame" | "validate" => "validate_frame".to_string(),
        other => other.to_string(),
    }
}

fn normalize_token(raw: &str) -> String {
    raw.trim().to_ascii_lowercase().replace('-', "_")
}

fn text_any(payload: &Value, keys: &[&str]) -> String {
    for key in keys {
        if let Some(value) = payload.get(*key).and_then(Value::as_str) {
            let trimmed = value.trim();
            if !trimmed.is_empty() {
                return trimmed.to_string();
            }
        }
    }
    String::new()
}

fn numeric_any(payload: &Value, keys: &[&str]) -> Option<u64> {
    for key in keys {
        match payload.get(*key) {
            Some(Value::Number(value)) => {
                if let Some(parsed) = value.as_u64() {
                    return Some(parsed);
                }
            }
            Some(Value::String(value)) => {
                if let Ok(parsed) = value.trim().parse::<u64>() {
                    return Some(parsed);
                }
            }
            _ => {}
        }
    }
    None
}

fn boolish(payload: &Value, key: &str, default: bool) -> bool {
    match payload.get(key) {
        Some(Value::Bool(value)) => *value,
        Some(Value::String(value)) => match value.trim().to_ascii_lowercase().as_str() {
            "1" | "true" | "yes" | "y" | "enabled" => true,
            "0" | "false" | "no" | "n" | "disabled" => false,
            _ => default,
        },
        _ => default,
    }
}

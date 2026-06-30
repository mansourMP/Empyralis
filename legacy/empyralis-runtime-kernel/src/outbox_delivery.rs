use serde_json::{json, Value};

const DEFAULT_LIMIT: u64 = 200;
const MAX_LIMIT: u64 = 500;
const DEFAULT_CLAIM_TTL_SECONDS: u64 = 30;
const DEFAULT_MAX_RETRIES: u64 = 5;

pub fn outbox_delivery_decision_command(payload: &Value) -> Value {
    let operation = normalize_operation(&text_any(payload, &["operation", "action"]));
    if operation.is_empty() {
        return block("outbox_operation_missing", "unknown");
    }
    if !known_operation(&operation) {
        return block("outbox_operation_unknown", &operation);
    }
    if bool_field(payload, "repository_read_only") && mutates_outbox(&operation) {
        return block("outbox_repository_read_only", &operation);
    }

    match operation.as_str() {
        "persist_event" => persist_event_decision(payload),
        "list_undelivered" => list_undelivered_decision(payload),
        "claim_due" => claim_due_decision(payload),
        "patch_payload" => patch_payload_decision(payload),
        "mark_delivered" => mark_delivered_decision(payload),
        "record_failure" => record_failure_decision(payload),
        "list_poisoned" => list_poisoned_decision(payload),
        "delivery_status" => delivery_status_decision(payload),
        _ => block("outbox_operation_unknown", &operation),
    }
}

fn persist_event_decision(payload: &Value) -> Value {
    let event_id = text_any(payload, &["event_id", "id"]);
    if event_id.is_empty() {
        return block("outbox_event_id_missing", "persist_event");
    }
    let event_type = text_any(payload, &["event_type", "type"]);
    if event_type.is_empty() {
        return block("outbox_event_type_missing", "persist_event");
    }
    let tenant_id = text_any(payload, &["tenant_id"]);
    if tenant_id.is_empty() {
        return block("tenant_id_missing", "persist_event");
    }
    let workspace_id = text_any(payload, &["workspace_id"]);
    if workspace_id.is_empty() {
        return block("workspace_id_missing", "persist_event");
    }
    allow(
        "outbox_persist_allowed",
        "persist_event",
        "persist_outbox_event",
        &event_id,
        &event_type,
        &tenant_id,
        &workspace_id,
        false,
        "security",
    )
}

fn list_undelivered_decision(payload: &Value) -> Value {
    allow(
        "outbox_list_undelivered_allowed",
        "list_undelivered",
        "list_undelivered_outbox_events",
        "",
        &text_any(payload, &["event_type"]),
        &text_any(payload, &["tenant_id"]),
        &text_any(payload, &["workspace_id"]),
        true,
        "standard",
    )
    .with_extra("limit", json!(limit(payload)))
    .with_extra(
        "older_than_seconds",
        json!(numeric_any(payload, &["older_than_seconds"]).unwrap_or(30)),
    )
}

fn claim_due_decision(payload: &Value) -> Value {
    let claimed_by = text_any(payload, &["claimed_by", "worker_id", "consumer_id"]);
    if claimed_by.is_empty() {
        return block("outbox_claimed_by_missing", "claim_due");
    }
    let claim_ttl_seconds = numeric_any(payload, &["claim_ttl_seconds", "ttl_seconds"])
        .unwrap_or(DEFAULT_CLAIM_TTL_SECONDS)
        .clamp(1, 3600);
    allow(
        "outbox_claim_due_allowed",
        "claim_due",
        "claim_due_outbox_events",
        "",
        &text_any(payload, &["event_type"]),
        &text_any(payload, &["tenant_id"]),
        &text_any(payload, &["workspace_id"]),
        false,
        "security",
    )
    .with_extra("claimed_by", json!(claimed_by))
    .with_extra("claim_ttl_seconds", json!(claim_ttl_seconds))
    .with_extra("limit", json!(limit(payload)))
}

fn patch_payload_decision(payload: &Value) -> Value {
    let event_id = text_any(payload, &["event_id", "id"]);
    if event_id.is_empty() {
        return block("outbox_event_id_missing", "patch_payload");
    }
    if bool_field(payload, "delivered") {
        return block("outbox_delivered_event_locked", "patch_payload");
    }
    if bool_field(payload, "poisoned") && !bool_field(payload, "repair_poisoned") {
        return block(
            "outbox_poisoned_event_requires_repair_flag",
            "patch_payload",
        );
    }
    if !bool_field(payload, "payload_patch_present")
        && !payload
            .get("payload_patch")
            .map(Value::is_object)
            .unwrap_or(false)
    {
        return block("outbox_payload_patch_missing", "patch_payload");
    }
    allow(
        "outbox_patch_payload_allowed",
        "patch_payload",
        "patch_outbox_event_payload",
        &event_id,
        &text_any(payload, &["event_type"]),
        &text_any(payload, &["tenant_id"]),
        &text_any(payload, &["workspace_id"]),
        false,
        "security",
    )
}

fn mark_delivered_decision(payload: &Value) -> Value {
    let event_id = text_any(payload, &["event_id", "id"]);
    if event_id.is_empty() {
        return block("outbox_event_id_missing", "mark_delivered");
    }
    let claim_token = text_any(payload, &["claim_token"]);
    if claim_token.is_empty() {
        return block("outbox_claim_token_missing", "mark_delivered");
    }
    if bool_field(payload, "claim_token_mismatch") {
        return block("outbox_claim_token_mismatch", "mark_delivered");
    }
    if bool_field(payload, "claim_expired") {
        return block("outbox_claim_expired", "mark_delivered");
    }
    if bool_field(payload, "delivered") {
        return allow(
            "outbox_already_delivered",
            "mark_delivered",
            "noop",
            &event_id,
            &text_any(payload, &["event_type"]),
            &text_any(payload, &["tenant_id"]),
            &text_any(payload, &["workspace_id"]),
            true,
            "standard",
        );
    }
    allow(
        "outbox_mark_delivered_allowed",
        "mark_delivered",
        "mark_outbox_event_delivered",
        &event_id,
        &text_any(payload, &["event_type"]),
        &text_any(payload, &["tenant_id"]),
        &text_any(payload, &["workspace_id"]),
        false,
        "security",
    )
}

fn record_failure_decision(payload: &Value) -> Value {
    let event_id = text_any(payload, &["event_id", "id"]);
    if event_id.is_empty() {
        return block("outbox_event_id_missing", "record_failure");
    }
    let claim_token = text_any(payload, &["claim_token"]);
    if claim_token.is_empty() {
        return block("outbox_claim_token_missing", "record_failure");
    }
    if bool_field(payload, "claim_token_mismatch") {
        return block("outbox_claim_token_mismatch", "record_failure");
    }
    if text_any(payload, &["error_text", "last_delivery_error", "error"]).is_empty() {
        return block("outbox_delivery_error_missing", "record_failure");
    }
    let retry_count = numeric_any(payload, &["retry_count"]).unwrap_or(0);
    let max_retries = numeric_any(payload, &["max_retries"])
        .unwrap_or(DEFAULT_MAX_RETRIES)
        .max(1);
    let poison = bool_field(payload, "poison") || retry_count.saturating_add(1) >= max_retries;
    let retry_delay_seconds = if poison {
        0
    } else {
        numeric_any(payload, &["retry_delay_seconds"])
            .unwrap_or_else(|| computed_retry_delay(retry_count))
    };
    allow(
        "outbox_record_failure_allowed",
        "record_failure",
        "record_outbox_delivery_failure",
        &event_id,
        &text_any(payload, &["event_type"]),
        &text_any(payload, &["tenant_id"]),
        &text_any(payload, &["workspace_id"]),
        false,
        "security",
    )
    .with_extra("poison", json!(poison))
    .with_extra("retry_delay_seconds", json!(retry_delay_seconds))
    .with_extra(
        "increment_retry",
        json!(!bool_field(payload, "do_not_increment_retry")),
    )
}

fn list_poisoned_decision(payload: &Value) -> Value {
    allow(
        "outbox_list_poisoned_allowed",
        "list_poisoned",
        "list_poisoned_outbox_events",
        "",
        &text_any(payload, &["event_type"]),
        &text_any(payload, &["tenant_id"]),
        &text_any(payload, &["workspace_id"]),
        true,
        "standard",
    )
    .with_extra("limit", json!(limit(payload)))
}

fn delivery_status_decision(payload: &Value) -> Value {
    allow(
        "outbox_delivery_status_allowed",
        "delivery_status",
        "get_outbox_delivery_status",
        "",
        &text_any(payload, &["event_type"]),
        &text_any(payload, &["tenant_id"]),
        &text_any(payload, &["workspace_id"]),
        true,
        "standard",
    )
}

fn allow(
    reason: &str,
    operation: &str,
    next_action: &str,
    event_id: &str,
    event_type: &str,
    tenant_id: &str,
    workspace_id: &str,
    cacheable: bool,
    audit_visibility: &str,
) -> Value {
    json!({
        "ok": true,
        "decision": "allow",
        "reason": reason,
        "operation": operation,
        "next_action": next_action,
        "event_id": event_id,
        "event_type": event_type,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "approval_required": false,
        "cacheable": cacheable,
        "audit_visibility": audit_visibility,
    })
}

fn block(reason: &str, operation: &str) -> Value {
    json!({
        "ok": false,
        "decision": "block",
        "reason": reason,
        "operation": operation,
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
        "persist" | "persist_outbox_event" => "persist_event".to_string(),
        "list" | "list_due" | "list_undelivered_outbox_events" => "list_undelivered".to_string(),
        "claim" | "claim_due_outbox_events" => "claim_due".to_string(),
        "patch" | "patch_outbox_event_payload" => "patch_payload".to_string(),
        "delivered" | "mark_outbox_event_delivered" => "mark_delivered".to_string(),
        "failure" | "record_outbox_delivery_failure" => "record_failure".to_string(),
        "poisoned" | "list_poisoned_outbox_events" => "list_poisoned".to_string(),
        "status" | "get_outbox_delivery_status" => "delivery_status".to_string(),
        other => other.to_string(),
    }
}

fn known_operation(operation: &str) -> bool {
    matches!(
        operation,
        "persist_event"
            | "list_undelivered"
            | "claim_due"
            | "patch_payload"
            | "mark_delivered"
            | "record_failure"
            | "list_poisoned"
            | "delivery_status"
    )
}

fn mutates_outbox(operation: &str) -> bool {
    matches!(
        operation,
        "persist_event" | "claim_due" | "patch_payload" | "mark_delivered" | "record_failure"
    )
}

fn limit(payload: &Value) -> u64 {
    numeric_any(payload, &["limit"])
        .unwrap_or(DEFAULT_LIMIT)
        .clamp(1, MAX_LIMIT)
}

fn computed_retry_delay(retry_count: u64) -> u64 {
    let capped = retry_count.min(8);
    let multiplier = 1_u64 << capped;
    (10 * multiplier).min(3600)
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

fn bool_field(payload: &Value, key: &str) -> bool {
    match payload.get(key) {
        Some(Value::Bool(value)) => *value,
        Some(Value::String(value)) => matches!(
            value.trim().to_ascii_lowercase().as_str(),
            "1" | "true" | "yes" | "y" | "enabled"
        ),
        _ => false,
    }
}

fn normalize_token(raw: &str) -> String {
    raw.trim().to_ascii_lowercase().replace('-', "_")
}

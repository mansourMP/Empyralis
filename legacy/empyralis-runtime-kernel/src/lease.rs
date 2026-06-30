use crate::policy::normalize_token;
use crate::protocol;
use serde_json::{json, Value};

const DEFAULT_LEASE_TTL_SECONDS: u64 = 900;
const MIN_LEASE_TTL_SECONDS: u64 = 30;
const MAX_LEASE_TTL_SECONDS: u64 = 3600;

pub fn machine_lease_decision_command(input: &Value) -> Value {
    let operation = normalize_operation(
        protocol::string_field(input, "operation")
            .or_else(|| protocol::string_field(input, "action"))
            .as_deref()
            .unwrap_or(""),
    );
    if operation.is_empty() {
        return protocol::block("lease_operation_missing");
    }

    let holder_id = protocol::string_field(input, "holder_id")
        .or_else(|| protocol::string_field(input, "requested_holder_id"));
    let current_holder_id = protocol::string_field(input, "current_holder_id");
    let lease_id = protocol::string_field(input, "lease_id");
    let current_lease_id = protocol::string_field(input, "current_lease_id")
        .or_else(|| protocol::string_field(input, "existing_lease_id"));
    let active_lease_count = input
        .get("active_lease_count")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let max_concurrent_leases = input
        .get("max_concurrent_leases")
        .and_then(Value::as_u64)
        .unwrap_or(1)
        .max(1);
    let stale = boolish(input, "stale") || boolish(input, "expired");
    let force = boolish(input, "force");
    let requested_ttl_seconds = input
        .get("lease_ttl_seconds")
        .or_else(|| input.get("requested_ttl_seconds"))
        .and_then(Value::as_u64)
        .unwrap_or(DEFAULT_LEASE_TTL_SECONDS)
        .clamp(MIN_LEASE_TTL_SECONDS, MAX_LEASE_TTL_SECONDS);

    match operation.as_str() {
        "acquire" => acquire_decision(
            holder_id,
            current_holder_id,
            lease_id,
            current_lease_id,
            active_lease_count,
            max_concurrent_leases,
            stale,
            force,
            requested_ttl_seconds,
        ),
        "renew" => renew_decision(
            holder_id,
            current_holder_id,
            lease_id,
            current_lease_id,
            stale,
            requested_ttl_seconds,
        ),
        "release" => release_decision(
            holder_id,
            current_holder_id,
            lease_id,
            current_lease_id,
            stale,
            force,
        ),
        "heartbeat" => heartbeat_decision(
            holder_id,
            current_holder_id,
            lease_id,
            current_lease_id,
            stale,
        ),
        _ => protocol::block("unknown_lease_operation"),
    }
}

fn acquire_decision(
    holder_id: Option<String>,
    current_holder_id: Option<String>,
    lease_id: Option<String>,
    current_lease_id: Option<String>,
    active_lease_count: u64,
    max_concurrent_leases: u64,
    stale: bool,
    force: bool,
    ttl_seconds: u64,
) -> Value {
    let Some(holder_id) = holder_id else {
        return protocol::block("lease_holder_missing");
    };
    if current_holder_id.as_deref() == Some(holder_id.as_str())
        && current_lease_id.as_ref().or(lease_id.as_ref()).is_some()
    {
        return lease_response(
            "allow",
            "lease_already_held_by_requester",
            "acquire",
            Some(holder_id),
            current_holder_id,
            current_lease_id.or(lease_id),
            ttl_seconds,
            false,
        );
    }
    if active_lease_count >= max_concurrent_leases && !stale && !force {
        return lease_response(
            "block",
            "lease_capacity_exhausted",
            "acquire",
            Some(holder_id),
            current_holder_id,
            lease_id,
            ttl_seconds,
            false,
        );
    }
    lease_response(
        if force && active_lease_count >= max_concurrent_leases {
            "require_approval"
        } else {
            "allow"
        },
        if stale {
            "stale_lease_may_be_replaced"
        } else if force && active_lease_count >= max_concurrent_leases {
            "forced_lease_takeover_requires_approval"
        } else {
            "lease_acquire_allowed"
        },
        "acquire",
        Some(holder_id),
        current_holder_id,
        lease_id,
        ttl_seconds,
        force,
    )
}

fn renew_decision(
    holder_id: Option<String>,
    current_holder_id: Option<String>,
    lease_id: Option<String>,
    current_lease_id: Option<String>,
    stale: bool,
    ttl_seconds: u64,
) -> Value {
    let Some(holder_id) = holder_id else {
        return protocol::block("lease_holder_missing");
    };
    if lease_id.is_none() {
        return protocol::block("lease_id_missing");
    }
    if current_lease_id.is_some() && lease_id.as_deref() != current_lease_id.as_deref() {
        return lease_response(
            "block",
            "lease_renew_id_mismatch",
            "renew",
            Some(holder_id),
            current_holder_id,
            lease_id,
            ttl_seconds,
            false,
        );
    }
    if current_holder_id.as_deref() != Some(holder_id.as_str()) {
        return lease_response(
            "block",
            "lease_not_held_by_requester",
            "renew",
            Some(holder_id),
            current_holder_id,
            lease_id,
            ttl_seconds,
            false,
        );
    }
    lease_response(
        if stale { "require_approval" } else { "allow" },
        if stale {
            "stale_lease_renewal_requires_approval"
        } else {
            "lease_renew_allowed"
        },
        "renew",
        Some(holder_id),
        current_holder_id,
        lease_id,
        ttl_seconds,
        false,
    )
}

fn release_decision(
    holder_id: Option<String>,
    current_holder_id: Option<String>,
    lease_id: Option<String>,
    current_lease_id: Option<String>,
    stale: bool,
    force: bool,
) -> Value {
    let Some(holder_id) = holder_id else {
        return protocol::block("lease_holder_missing");
    };
    if lease_id.is_none() {
        return protocol::block("lease_id_missing");
    }
    if current_lease_id.is_some() && lease_id.as_deref() != current_lease_id.as_deref() {
        return lease_response(
            "block",
            "lease_release_id_mismatch",
            "release",
            Some(holder_id),
            current_holder_id,
            lease_id,
            0,
            false,
        );
    }
    if current_holder_id.as_deref() != Some(holder_id.as_str()) && !force {
        return lease_response(
            "block",
            "lease_release_not_owned",
            "release",
            Some(holder_id),
            current_holder_id,
            lease_id,
            0,
            false,
        );
    }
    lease_response(
        if force && current_holder_id.as_deref() != Some(holder_id.as_str()) {
            "require_approval"
        } else {
            "allow"
        },
        if force && current_holder_id.as_deref() != Some(holder_id.as_str()) {
            "forced_release_requires_approval"
        } else if stale {
            "stale_lease_release_allowed"
        } else {
            "lease_release_allowed"
        },
        "release",
        Some(holder_id),
        current_holder_id,
        lease_id,
        0,
        force,
    )
}

fn heartbeat_decision(
    holder_id: Option<String>,
    current_holder_id: Option<String>,
    lease_id: Option<String>,
    current_lease_id: Option<String>,
    stale: bool,
) -> Value {
    let Some(holder_id) = holder_id else {
        return protocol::block("lease_holder_missing");
    };
    if lease_id.is_none() {
        return protocol::block("lease_id_missing");
    }
    if current_lease_id.is_some() && lease_id.as_deref() != current_lease_id.as_deref() {
        return lease_response(
            "block",
            "lease_heartbeat_id_mismatch",
            "heartbeat",
            Some(holder_id),
            current_holder_id,
            lease_id,
            0,
            false,
        );
    }
    if current_holder_id.as_deref() != Some(holder_id.as_str()) {
        return lease_response(
            "block",
            "lease_heartbeat_not_owned",
            "heartbeat",
            Some(holder_id),
            current_holder_id,
            lease_id,
            0,
            false,
        );
    }
    lease_response(
        if stale { "block" } else { "allow" },
        if stale {
            "stale_lease_heartbeat_blocked"
        } else {
            "lease_heartbeat_allowed"
        },
        "heartbeat",
        Some(holder_id),
        current_holder_id,
        lease_id,
        0,
        false,
    )
}

fn lease_response(
    decision: &str,
    reason: &str,
    operation: &str,
    holder_id: Option<String>,
    current_holder_id: Option<String>,
    lease_id: Option<String>,
    ttl_seconds: u64,
    force: bool,
) -> Value {
    json!({
        "ok": decision != "block",
        "decision": decision,
        "reason": reason,
        "operation": operation,
        "next_action": next_action(operation, decision),
        "holder_id": holder_id,
        "current_holder_id": current_holder_id,
        "lease_id": lease_id,
        "ttl_seconds": ttl_seconds,
        "force": force,
        "approval_required": decision == "require_approval",
        "cacheable": false,
        "audit_visibility": if decision == "allow" { "standard" } else { "security" },
    })
}

fn next_action(operation: &str, decision: &str) -> &'static str {
    match (operation, decision) {
        (_, "require_approval") => "request_machine_lease_approval",
        ("acquire", "allow") | ("renew", "allow") => "persist_machine_lease_transition",
        ("release", "allow") => "release_machine_lease_transition",
        ("heartbeat", "allow") => "touch_machine_lease",
        _ => "deny_machine_lease_transition",
    }
}

fn normalize_operation(value: &str) -> String {
    match normalize_token(value).as_str() {
        "acquire" | "claim" | "create" => "acquire".to_string(),
        "renew" | "extend" => "renew".to_string(),
        "release" | "delete" | "free" => "release".to_string(),
        "heartbeat" | "ping" | "keepalive" => "heartbeat".to_string(),
        _ => String::new(),
    }
}

fn boolish(input: &Value, key: &str) -> bool {
    match input.get(key) {
        Some(Value::Bool(value)) => *value,
        Some(Value::String(value)) => matches!(
            normalize_token(value).as_str(),
            "1" | "true" | "yes" | "on" | "enabled" | "active" | "force" | "forced"
        ),
        Some(Value::Number(value)) => value.as_u64().unwrap_or(0) > 0,
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_operation_blocks() {
        let response = machine_lease_decision_command(&json!({}));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "lease_operation_missing");
    }

    #[test]
    fn acquire_allows_when_capacity_available() {
        let response = machine_lease_decision_command(&json!({
            "operation": "acquire",
            "holder_id": "worker-1",
            "active_lease_count": 0,
            "max_concurrent_leases": 1,
            "lease_ttl_seconds": 999999
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["ttl_seconds"], MAX_LEASE_TTL_SECONDS);
    }

    #[test]
    fn acquire_blocks_when_capacity_exhausted() {
        let response = machine_lease_decision_command(&json!({
            "operation": "acquire",
            "holder_id": "worker-1",
            "active_lease_count": 1,
            "max_concurrent_leases": 1
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "lease_capacity_exhausted");
    }

    #[test]
    fn forced_takeover_requires_approval() {
        let response = machine_lease_decision_command(&json!({
            "operation": "acquire",
            "holder_id": "worker-2",
            "current_holder_id": "worker-1",
            "lease_id": "lease-1",
            "active_lease_count": 1,
            "max_concurrent_leases": 1,
            "force": true
        }));
        assert_eq!(response["decision"], "require_approval");
        assert_eq!(response["approval_required"], true);
    }

    #[test]
    fn renew_blocks_non_owner() {
        let response = machine_lease_decision_command(&json!({
            "operation": "renew",
            "holder_id": "worker-2",
            "current_holder_id": "worker-1",
            "lease_id": "lease-1"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "lease_not_held_by_requester");
    }

    #[test]
    fn release_allows_owner() {
        let response = machine_lease_decision_command(&json!({
            "operation": "release",
            "holder_id": "worker-1",
            "current_holder_id": "worker-1",
            "lease_id": "lease-1"
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["reason"], "lease_release_allowed");
        assert_eq!(response["next_action"], "release_machine_lease_transition");
    }

    #[test]
    fn release_blocks_lease_id_mismatch() {
        let response = machine_lease_decision_command(&json!({
            "operation": "release",
            "holder_id": "worker-1",
            "current_holder_id": "worker-1",
            "lease_id": "lease-2",
            "current_lease_id": "lease-1"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "lease_release_id_mismatch");
    }

    #[test]
    fn stale_release_uses_rust_owned_reason() {
        let response = machine_lease_decision_command(&json!({
            "operation": "release",
            "holder_id": "worker-1",
            "current_holder_id": "worker-1",
            "lease_id": "lease-1",
            "current_lease_id": "lease-1",
            "stale": true
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["reason"], "stale_lease_release_allowed");
    }
}

use serde_json::{json, Value};

const TERMINAL_STATES: &[&str] = &[
    "completed",
    "succeeded",
    "failed",
    "cancelled",
    "canceled",
    "timeout",
    "timed_out",
    "archived",
];

pub fn run_record_decision_command(payload: &Value) -> Value {
    let operation = normalize_operation(&text_any(payload, &["operation", "action"]));
    if operation.is_empty() {
        return block("run_record_operation_missing", "unknown");
    }
    if !known_operation(&operation) {
        return block("run_record_operation_unknown", &operation);
    }

    let run_id = text_any(payload, &["run_id", "id"]);
    if run_id.is_empty() {
        return block("run_id_missing", &operation);
    }

    let workspace_id = text_any(payload, &["workspace_id", "owner_workspace_id"]);
    if requires_workspace(&operation) && workspace_id.is_empty() {
        return block("workspace_id_missing", &operation);
    }

    let tenant_id = text_any(payload, &["tenant_id", "owner_tenant_id"]);
    if requires_tenant(&operation) && tenant_id.is_empty() {
        return block("tenant_id_missing", &operation);
    }

    if bool_field(payload, "version_conflict") {
        return block("run_record_version_conflict", &operation);
    }
    if bool_field(payload, "repository_read_only") && mutates_repository(&operation) {
        return block("run_record_repository_read_only", &operation);
    }
    if bool_field(payload, "kill_switch_enabled") && mutates_runtime(&operation) {
        return block("run_record_kill_switch_enabled", &operation);
    }

    match operation.as_str() {
        "register_live_run" => {
            register_live_run_decision(payload, &run_id, &tenant_id, &workspace_id)
        }
        "persist_snapshot" => {
            persist_snapshot_decision(payload, &run_id, &tenant_id, &workspace_id)
        }
        "record_transition" => {
            record_transition_decision(payload, &run_id, &tenant_id, &workspace_id)
        }
        "archive_payload" => archive_payload_decision(payload, &run_id, &tenant_id, &workspace_id),
        "emit_transition_outbox" => {
            emit_transition_outbox_decision(payload, &run_id, &tenant_id, &workspace_id)
        }
        "emit_artifact_outbox" => {
            emit_artifact_outbox_decision(payload, &run_id, &tenant_id, &workspace_id)
        }
        "activate_live_run" => {
            activate_live_run_decision(payload, &run_id, &tenant_id, &workspace_id)
        }
        _ => block("run_record_operation_unknown", &operation),
    }
}

fn register_live_run_decision(
    payload: &Value,
    run_id: &str,
    tenant_id: &str,
    workspace_id: &str,
) -> Value {
    let state = normalize_state(&text_any(payload, &["state", "status", "initial_state"]));
    let initial_state = if state.is_empty() {
        "queued"
    } else {
        state.as_str()
    };
    if is_terminal(initial_state) {
        return block("run_record_initial_state_terminal", "register_live_run");
    }
    if bool_field(payload, "duplicate_run_id") {
        return block("run_record_duplicate_run_id", "register_live_run");
    }
    with_record_patch(
        allow(
            "run_record_register_allowed",
            "register_live_run",
            "create_live_run_initial",
            run_id,
            tenant_id,
            workspace_id,
            "unknown",
            initial_state,
            version(payload),
            version(payload),
            "security",
            false,
        ),
        json!({
            "status": initial_state,
            "state": initial_state,
            "_event_seq": version(payload),
            "_archived": false,
        }),
    )
}

fn persist_snapshot_decision(
    payload: &Value,
    run_id: &str,
    tenant_id: &str,
    workspace_id: &str,
) -> Value {
    let state = normalize_state(&text_any(payload, &["state", "status"]));
    if state.is_empty() {
        return block("run_record_state_missing", "persist_snapshot");
    }
    let expected_version = version(payload);
    let current_version = numeric_any(payload, &["current_version", "durable_version"]);
    if let Some(current) = current_version {
        if current != expected_version {
            return block("run_record_expected_version_mismatch", "persist_snapshot");
        }
    }
    with_record_patch(
        allow(
            "run_record_snapshot_allowed",
            "persist_snapshot",
            "update_live_run_if_version_matches",
            run_id,
            tenant_id,
            workspace_id,
            &state,
            &state,
            expected_version,
            expected_version + 1,
            "security",
            false,
        ),
        json!({
            "status": state,
            "state": state,
            "_event_seq": expected_version,
        }),
    )
}

fn record_transition_decision(
    payload: &Value,
    run_id: &str,
    tenant_id: &str,
    workspace_id: &str,
) -> Value {
    let from_state = normalize_state(&text_any(
        payload,
        &["from_state", "previous_state", "current_state"],
    ));
    let to_state = normalize_state(&text_any(
        payload,
        &["to_state", "next_state", "state", "status"],
    ));
    if from_state.is_empty() || to_state.is_empty() {
        return block("run_record_transition_state_missing", "record_transition");
    }
    if from_state == to_state {
        return allow(
            "run_record_transition_noop",
            "record_transition",
            "noop",
            run_id,
            tenant_id,
            workspace_id,
            &from_state,
            &to_state,
            version(payload),
            version(payload),
            "standard",
            true,
        );
    }
    if is_terminal(&from_state) {
        return block("run_record_terminal_transition_locked", "record_transition");
    }
    if !allowed_transition(&from_state, &to_state) {
        return block("run_record_transition_invalid", "record_transition");
    }
    with_record_patch(
        allow(
            "run_record_transition_allowed",
            "record_transition",
            "record_transition",
            run_id,
            tenant_id,
            workspace_id,
            &from_state,
            &to_state,
            version(payload),
            version(payload),
            "security",
            false,
        ),
        json!({
            "status": to_state,
            "state": to_state,
            "_event_seq": version(payload),
        }),
    )
}

fn archive_payload_decision(
    payload: &Value,
    run_id: &str,
    tenant_id: &str,
    workspace_id: &str,
) -> Value {
    let final_state = normalize_state(&text_any(payload, &["final_state", "state", "status"]));
    if final_state.is_empty() {
        return block("run_record_final_state_missing", "archive_payload");
    }
    if !is_terminal(&final_state) {
        return block(
            "run_record_archive_requires_terminal_state",
            "archive_payload",
        );
    }
    with_record_patch(
        allow(
            "run_record_archive_allowed",
            "archive_payload",
            "archive_run",
            run_id,
            tenant_id,
            workspace_id,
            &final_state,
            "archived",
            version(payload),
            version(payload),
            "security",
            false,
        ),
        json!({
            "status": final_state,
            "state": final_state,
            "_archived": true,
            "_event_seq": version(payload),
        }),
    )
}

fn emit_transition_outbox_decision(
    payload: &Value,
    run_id: &str,
    tenant_id: &str,
    workspace_id: &str,
) -> Value {
    let from_state = normalize_state(&text_any(payload, &["from_state", "previous_state"]));
    let to_state = normalize_state(&text_any(
        payload,
        &["to_state", "next_state", "state", "status"],
    ));
    if from_state.is_empty() || to_state.is_empty() {
        return block("run_record_outbox_state_missing", "emit_transition_outbox");
    }
    let next_action = if from_state == to_state {
        "noop"
    } else {
        "emit_run_transition_event"
    };
    allow(
        "run_record_transition_outbox_allowed",
        "emit_transition_outbox",
        next_action,
        run_id,
        tenant_id,
        workspace_id,
        &from_state,
        &to_state,
        version(payload),
        version(payload),
        "standard",
        from_state == to_state,
    )
}

fn emit_artifact_outbox_decision(
    payload: &Value,
    run_id: &str,
    tenant_id: &str,
    workspace_id: &str,
) -> Value {
    let artifact_count = numeric_any(payload, &["artifact_count", "artifacts_count"]).unwrap_or(0);
    let next_action = if artifact_count == 0 {
        "noop"
    } else {
        "emit_artifact_created_events"
    };
    allow(
        "run_record_artifact_outbox_allowed",
        "emit_artifact_outbox",
        next_action,
        run_id,
        tenant_id,
        workspace_id,
        "",
        "",
        version(payload),
        version(payload),
        "standard",
        artifact_count == 0,
    )
    .as_object()
    .map(|object| {
        let mut mapped = object.clone();
        mapped.insert("artifact_count".to_string(), json!(artifact_count));
        Value::Object(mapped)
    })
    .unwrap_or_else(|| {
        block(
            "run_record_invalid_artifact_outbox_response",
            "emit_artifact_outbox",
        )
    })
}

fn activate_live_run_decision(
    payload: &Value,
    run_id: &str,
    tenant_id: &str,
    workspace_id: &str,
) -> Value {
    let selected_target =
        normalize_token(&text_any(payload, &["selected_target", "execution_target"]));
    if selected_target.is_empty() {
        return block("run_record_selected_target_missing", "activate_live_run");
    }
    let local_target = normalize_token(&text_any(
        payload,
        &["local_companion_target", "local_target"],
    ));
    let defer_local_enqueue = bool_field(payload, "defer_local_enqueue");
    let next_action = if !local_target.is_empty() && selected_target == local_target {
        if defer_local_enqueue {
            "hydrate_local_memory_context"
        } else {
            "enqueue_local_companion_run"
        }
    } else {
        "start_background_run"
    };
    allow(
        "run_record_activation_allowed",
        "activate_live_run",
        next_action,
        run_id,
        tenant_id,
        workspace_id,
        "",
        "running",
        version(payload),
        version(payload),
        "runtime",
        false,
    )
    .as_object()
    .map(|object| {
        let mut mapped = object.clone();
        mapped.insert("selected_target".to_string(), json!(selected_target));
        mapped.insert("local_companion_target".to_string(), json!(local_target));
        mapped.insert(
            "defer_local_enqueue".to_string(),
            json!(defer_local_enqueue),
        );
        Value::Object(mapped)
    })
    .unwrap_or_else(|| {
        block(
            "run_record_invalid_activation_response",
            "activate_live_run",
        )
    })
}

fn allow(
    reason: &str,
    operation: &str,
    next_action: &str,
    run_id: &str,
    tenant_id: &str,
    workspace_id: &str,
    from_state: &str,
    to_state: &str,
    expected_version: u64,
    next_version: u64,
    audit_visibility: &str,
    cacheable: bool,
) -> Value {
    json!({
        "ok": true,
        "decision": "allow",
        "reason": reason,
        "operation": operation,
        "next_action": next_action,
        "run_id": run_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "from_state": from_state,
        "to_state": to_state,
        "expected_version": expected_version,
        "next_version": next_version,
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

fn with_record_patch(decision: Value, record_patch: Value) -> Value {
    decision
        .as_object()
        .map(|object| {
            let mut mapped = object.clone();
            mapped.insert("record_patch".to_string(), record_patch);
            Value::Object(mapped)
        })
        .unwrap_or_else(|| block("run_record_invalid_record_patch_response", "unknown"))
}

fn normalize_operation(raw: &str) -> String {
    match normalize_token(raw).as_str() {
        "register" | "create_live_run" | "create_live_run_initial" => {
            "register_live_run".to_string()
        }
        "snapshot" | "persist_live_run_state" | "update_live_run_if_version_matches" => {
            "persist_snapshot".to_string()
        }
        "transition" | "write_transition" => "record_transition".to_string(),
        "archive" | "archive_run" => "archive_payload".to_string(),
        "transition_outbox" | "run_transition_outbox" => "emit_transition_outbox".to_string(),
        "artifact_outbox" | "artifact_created_outbox" => "emit_artifact_outbox".to_string(),
        "activate" | "activate_run" => "activate_live_run".to_string(),
        other => other.to_string(),
    }
}

fn known_operation(operation: &str) -> bool {
    matches!(
        operation,
        "register_live_run"
            | "persist_snapshot"
            | "record_transition"
            | "archive_payload"
            | "emit_transition_outbox"
            | "emit_artifact_outbox"
            | "activate_live_run"
    )
}

fn requires_workspace(operation: &str) -> bool {
    !matches!(operation, "activate_live_run")
}

fn requires_tenant(operation: &str) -> bool {
    !matches!(operation, "activate_live_run")
}

fn mutates_repository(operation: &str) -> bool {
    matches!(
        operation,
        "register_live_run" | "persist_snapshot" | "record_transition" | "archive_payload"
    )
}

fn mutates_runtime(operation: &str) -> bool {
    matches!(
        operation,
        "register_live_run"
            | "persist_snapshot"
            | "record_transition"
            | "archive_payload"
            | "activate_live_run"
    )
}

fn normalize_state(raw: &str) -> String {
    match normalize_token(raw).as_str() {
        "canceled" => "cancelled".to_string(),
        "timed_out" => "timeout".to_string(),
        "succeeded" => "completed".to_string(),
        "waiting" | "hitl_wait" => "waiting_for_input".to_string(),
        other => other.to_string(),
    }
}

fn normalize_token(raw: &str) -> String {
    raw.trim().to_ascii_lowercase().replace('-', "_")
}

fn is_terminal(state: &str) -> bool {
    TERMINAL_STATES.contains(&state)
}

fn allowed_transition(from_state: &str, to_state: &str) -> bool {
    matches!(
        (from_state, to_state),
        ("unknown", "queued")
            | ("none", "queued")
            | ("queued", "running")
            | ("queued", "awaiting_approval")
            | ("queued", "cancelled")
            | ("awaiting_approval", "approved")
            | ("awaiting_approval", "cancelled")
            | ("approved", "running")
            | ("approved", "queued")
            | ("running", "waiting_for_input")
            | ("waiting_for_input", "running")
            | ("running", "completed")
            | ("running", "failed")
            | ("running", "cancelled")
            | ("running", "timeout")
            | ("failed", "queued")
            | ("timeout", "queued")
    )
}

fn version(payload: &Value) -> u64 {
    numeric_any(payload, &["expected_version", "version", "durable_version"]).unwrap_or(0)
}

fn numeric_any(payload: &Value, keys: &[&str]) -> Option<u64> {
    for key in keys {
        match payload.get(key) {
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

fn text_any(payload: &Value, keys: &[&str]) -> String {
    for key in keys {
        if let Some(value) = payload.get(key).and_then(Value::as_str) {
            let trimmed = value.trim();
            if !trimmed.is_empty() {
                return trimmed.to_string();
            }
        }
    }
    String::new()
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

#[cfg(test)]
mod run_record_kernel_boundary_tests {
    use super::run_record_decision_command;

    #[test]
    fn register_live_run_blocks_terminal_initial_state() {
        let decision = run_record_decision_command(&serde_json::json!({
            "operation": "register_live_run",
            "run_id": "run-1",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "initial_state": "completed"
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("block")
        );
        assert_eq!(
            decision.get("reason").and_then(|value| value.as_str()),
            Some("run_record_initial_state_terminal")
        );
    }

    #[test]
    fn persist_snapshot_blocks_expected_version_mismatch() {
        let decision = run_record_decision_command(&serde_json::json!({
            "operation": "persist_snapshot",
            "run_id": "run-1",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "status": "running",
            "version": 3,
            "current_version": 2
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("block")
        );
        assert_eq!(
            decision.get("reason").and_then(|value| value.as_str()),
            Some("run_record_expected_version_mismatch")
        );
    }

    #[test]
    fn register_live_run_emits_record_patch() {
        let decision = run_record_decision_command(&serde_json::json!({
            "operation": "register_live_run",
            "run_id": "run-1",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "initial_state": "starting",
            "version": 4
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("allow")
        );
        assert_eq!(
            decision
                .get("record_patch")
                .and_then(|value| value.get("status"))
                .and_then(|value| value.as_str()),
            Some("starting")
        );
        assert_eq!(
            decision
                .get("record_patch")
                .and_then(|value| value.get("_event_seq"))
                .and_then(|value| value.as_u64()),
            Some(4)
        );
    }

    #[test]
    fn persist_snapshot_emits_record_patch() {
        let decision = run_record_decision_command(&serde_json::json!({
            "operation": "persist_snapshot",
            "run_id": "run-1",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "status": "running",
            "version": 7,
            "current_version": 7
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("allow")
        );
        assert_eq!(
            decision
                .get("record_patch")
                .and_then(|value| value.get("state"))
                .and_then(|value| value.as_str()),
            Some("running")
        );
        assert_eq!(
            decision
                .get("record_patch")
                .and_then(|value| value.get("_event_seq"))
                .and_then(|value| value.as_u64()),
            Some(7)
        );
    }

    #[test]
    fn archive_payload_emits_record_patch() {
        let decision = run_record_decision_command(&serde_json::json!({
            "operation": "archive_payload",
            "run_id": "run-1",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "final_state": "completed",
            "version": 9
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("allow")
        );
        assert_eq!(
            decision
                .get("record_patch")
                .and_then(|value| value.get("_archived"))
                .and_then(|value| value.as_bool()),
            Some(true)
        );
        assert_eq!(
            decision
                .get("record_patch")
                .and_then(|value| value.get("status"))
                .and_then(|value| value.as_str()),
            Some("completed")
        );
    }
}

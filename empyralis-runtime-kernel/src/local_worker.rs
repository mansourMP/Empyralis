use serde_json::{json, Value};

const TERMINAL_RUN_STATUSES: &[&str] = &[
    "completed",
    "succeeded",
    "failed",
    "cancelled",
    "canceled",
    "timeout",
    "timed_out",
];

pub fn local_worker_decision_command(input: &Value) -> Value {
    let operation = normalize_operation(
        string_field(input, "operation")
            .or_else(|| string_field(input, "action"))
            .or_else(|| string_field(input, "intent"))
            .as_deref()
            .unwrap_or(""),
    );
    if operation.is_empty() {
        return block("local_worker_operation_missing");
    }

    let workspace_id = string_field(input, "workspace_id");
    let worker_id = string_field(input, "worker_id").or_else(|| string_field(input, "runtime_id"));
    let current_worker_id = string_field(input, "current_worker_id")
        .or_else(|| string_field(input, "lease_holder_id"))
        .or_else(|| string_field(input, "claimed_worker_id"));
    let run_id = string_field(input, "run_id");
    let run_status = normalize_token(
        string_field(input, "run_status")
            .or_else(|| string_field(input, "status"))
            .as_deref()
            .unwrap_or(""),
    );
    let actor_role = normalize_actor_role(
        string_field(input, "actor_role")
            .or_else(|| string_field(input, "user_role"))
            .or_else(|| string_field(input, "role"))
            .as_deref()
            .unwrap_or("worker"),
    );

    if workspace_id.is_none() && requires_workspace(&operation) {
        return block("workspace_id_missing");
    }
    if !boolish(input, "local_companion_enabled", true) && mutates_queue(&operation) {
        return block("local_companion_disabled");
    }
    if boolish(input, "kill_switch_active", false) && mutates_queue(&operation) {
        return block("local_worker_kill_switch_active");
    }
    if worker_id.is_none() && requires_worker(&operation) {
        return block("worker_id_missing");
    }
    if run_id.is_none() && requires_run(&operation) {
        return block("run_id_missing");
    }

    match operation.as_str() {
        "get_queue" => get_queue_decision(input, workspace_id, worker_id, run_id, actor_role),
        "cleanup_queue" => {
            cleanup_queue_decision(input, workspace_id, worker_id, run_id, actor_role)
        }
        "worker_status" => read_decision(
            "local_worker_status_allowed",
            "worker_status",
            "read_worker_status",
            workspace_id,
            worker_id,
            run_id,
            &actor_role,
            true,
        ),
        "worker_heartbeat" | "runtime_heartbeat" => worker_heartbeat_decision(
            input,
            &operation,
            workspace_id,
            worker_id,
            run_id,
            actor_role,
        ),
        "claim_run" => claim_run_decision(input, workspace_id, worker_id, run_id, actor_role),
        "run_heartbeat" => run_heartbeat_decision(
            input,
            workspace_id,
            worker_id,
            current_worker_id,
            run_id,
            actor_role,
            &run_status,
        ),
        "control_state" => control_state_decision(
            input,
            workspace_id,
            worker_id,
            current_worker_id,
            run_id,
            actor_role,
            &run_status,
        ),
        "complete_run" => complete_run_decision(
            input,
            workspace_id,
            worker_id,
            current_worker_id,
            run_id,
            actor_role,
            &run_status,
        ),
        "pause_run" => pause_run_decision(
            input,
            workspace_id,
            worker_id,
            current_worker_id,
            run_id,
            actor_role,
            &run_status,
        ),
        "fail_run" => fail_run_decision(
            input,
            workspace_id,
            worker_id,
            current_worker_id,
            run_id,
            actor_role,
            &run_status,
        ),
        "execute_command" => execute_command_decision(
            input,
            workspace_id,
            worker_id,
            run_id,
            &actor_role,
        ),
        _ => block("local_worker_operation_unknown"),
    }
}

fn get_queue_decision(
    input: &Value,
    workspace_id: Option<String>,
    worker_id: Option<String>,
    run_id: Option<String>,
    actor_role: String,
) -> Value {
    if !can_read(&actor_role) {
        return block("local_worker_actor_cannot_read");
    }
    let limit = positive_i64(input, "limit", 50);
    if !(1..=300).contains(&limit) {
        return block("local_queue_limit_invalid");
    }
    allow(
        "local_queue_read_allowed",
        "get_queue",
        "read_local_queue",
        workspace_id,
        worker_id,
        run_id,
        &actor_role,
        true,
        "standard",
    )
}

fn cleanup_queue_decision(
    input: &Value,
    workspace_id: Option<String>,
    worker_id: Option<String>,
    run_id: Option<String>,
    actor_role: String,
) -> Value {
    if !can_admin(&actor_role) {
        return block("local_worker_actor_cannot_cleanup");
    }
    let older_than_seconds = positive_i64(input, "older_than_seconds", 600);
    if !(60..=604800).contains(&older_than_seconds) {
        return block("local_queue_cleanup_threshold_invalid");
    }
    let limit = positive_i64(input, "limit", 200);
    if !(1..=1000).contains(&limit) {
        return block("local_queue_cleanup_limit_invalid");
    }
    if !boolish(input, "dry_run", true) && !boolish(input, "operator_confirmed", false) {
        return require_approval(
            "local_queue_cleanup_requires_confirmation",
            "cleanup_queue",
            "request_cleanup_confirmation",
            workspace_id,
            worker_id,
            run_id,
            &actor_role,
        );
    }
    allow(
        "local_queue_cleanup_allowed",
        "cleanup_queue",
        "cleanup_stale_local_queue",
        workspace_id,
        worker_id,
        run_id,
        &actor_role,
        false,
        "security",
    )
}

fn worker_heartbeat_decision(
    input: &Value,
    operation: &str,
    workspace_id: Option<String>,
    worker_id: Option<String>,
    run_id: Option<String>,
    actor_role: String,
) -> Value {
    if boolish(input, "runtime_role_specialist", false)
        && string_field(input, "install_id").is_none()
        && string_field(input, "specialist_key").is_none()
    {
        return block("specialist_runtime_identity_missing");
    }
    if boolish(input, "permission_probe_present", false)
        && boolish(input, "permission_probe_invalid", false)
    {
        return block("permission_probe_invalid");
    }
    allow(
        "local_worker_heartbeat_allowed",
        operation,
        if operation == "runtime_heartbeat" {
            "record_runtime_heartbeat"
        } else {
            "record_worker_heartbeat"
        },
        workspace_id,
        worker_id,
        run_id,
        &actor_role,
        false,
        "standard",
    )
}

fn claim_run_decision(
    input: &Value,
    workspace_id: Option<String>,
    worker_id: Option<String>,
    run_id: Option<String>,
    actor_role: String,
) -> Value {
    if boolish(input, "worker_revoked", false) {
        return block("local_worker_revoked");
    }
    if boolish(input, "capability_mismatch", false) {
        return block("local_worker_capability_mismatch");
    }
    if boolish(input, "queue_empty", false) {
        return allow(
            "local_queue_empty",
            "claim_run",
            "return_backpressure",
            workspace_id,
            worker_id,
            run_id,
            &actor_role,
            true,
            "standard",
        );
    }
    allow(
        "local_run_claim_allowed",
        "claim_run",
        "claim_local_run",
        workspace_id,
        worker_id,
        run_id,
        &actor_role,
        false,
        "security",
    )
}

fn run_heartbeat_decision(
    input: &Value,
    workspace_id: Option<String>,
    worker_id: Option<String>,
    current_worker_id: Option<String>,
    run_id: Option<String>,
    actor_role: String,
    run_status: &str,
) -> Value {
    if !run_is_active(run_status) {
        return block("local_run_heartbeat_state_invalid");
    }
    if boolish(input, "claim_missing", false) {
        return block("local_run_claim_missing");
    }
    if worker_mismatch(&worker_id, &current_worker_id) {
        return block("local_run_not_owned_by_worker");
    }
    allow(
        "local_run_heartbeat_allowed",
        "run_heartbeat",
        "record_run_heartbeat",
        workspace_id,
        worker_id,
        run_id,
        &actor_role,
        false,
        "standard",
    )
}

fn control_state_decision(
    input: &Value,
    workspace_id: Option<String>,
    worker_id: Option<String>,
    current_worker_id: Option<String>,
    run_id: Option<String>,
    actor_role: String,
    run_status: &str,
) -> Value {
    if boolish(input, "run_missing", false) {
        return block("run_not_found");
    }
    if worker_mismatch(&worker_id, &current_worker_id) {
        return block("local_run_not_owned_by_worker");
    }
    if TERMINAL_RUN_STATUSES.contains(&run_status) {
        return allow(
            "local_run_control_state_terminal",
            "control_state",
            "return_terminal_control_state",
            workspace_id,
            worker_id,
            run_id,
            &actor_role,
            true,
            "standard",
        );
    }
    allow(
        "local_run_control_state_allowed",
        "control_state",
        "read_run_control_state",
        workspace_id,
        worker_id,
        run_id,
        &actor_role,
        false,
        "standard",
    )
}

fn complete_run_decision(
    input: &Value,
    workspace_id: Option<String>,
    worker_id: Option<String>,
    current_worker_id: Option<String>,
    run_id: Option<String>,
    actor_role: String,
    run_status: &str,
) -> Value {
    if !run_is_active(run_status) {
        return block("local_run_complete_state_invalid");
    }
    if boolish(input, "claim_missing", false) {
        return block("local_run_claim_missing");
    }
    if worker_mismatch(&worker_id, &current_worker_id) {
        return block("local_run_not_owned_by_worker");
    }
    if boolish(input, "result_required", false)
        && string_field(input, "result_text").is_none()
        && input.get("result_data").is_none()
    {
        return block("local_run_result_missing");
    }
    allow(
        "local_run_complete_allowed",
        "complete_run",
        "complete_local_run",
        workspace_id,
        worker_id,
        run_id,
        &actor_role,
        false,
        "security",
    )
}

fn pause_run_decision(
    input: &Value,
    workspace_id: Option<String>,
    worker_id: Option<String>,
    current_worker_id: Option<String>,
    run_id: Option<String>,
    actor_role: String,
    run_status: &str,
) -> Value {
    if !run_is_active(run_status) {
        return block("local_run_pause_state_invalid");
    }
    if boolish(input, "claim_missing", false) {
        return block("local_run_claim_missing");
    }
    if worker_mismatch(&worker_id, &current_worker_id) {
        return block("local_run_not_owned_by_worker");
    }
    if string_field(input, "wait_reason").is_none() && input.get("browser_checkpoint").is_none() {
        return block("local_run_pause_reason_missing");
    }
    allow(
        "local_run_pause_allowed",
        "pause_run",
        "pause_local_run",
        workspace_id,
        worker_id,
        run_id,
        &actor_role,
        false,
        "security",
    )
}

fn fail_run_decision(
    input: &Value,
    workspace_id: Option<String>,
    worker_id: Option<String>,
    current_worker_id: Option<String>,
    run_id: Option<String>,
    actor_role: String,
    run_status: &str,
) -> Value {
    if !run_is_active(run_status) {
        return block("local_run_fail_state_invalid");
    }
    if boolish(input, "claim_missing", false) {
        return block("local_run_claim_missing");
    }
    if worker_mismatch(&worker_id, &current_worker_id) {
        return block("local_run_not_owned_by_worker");
    }
    if string_field(input, "error").is_none() {
        return block("local_run_error_missing");
    }
    allow(
        "local_run_fail_allowed",
        "fail_run",
        "fail_local_run",
        workspace_id,
        worker_id,
        run_id,
        &actor_role,
        false,
        "security",
    )
}

// ── Hard-blocked command patterns (ALWAYS enforced) ──
// Mirrors empyralis-supervisor shell.rs HARD_BLOCKED_COMMAND_PATTERNS.
// These catastrophic commands are NEVER allowed, even in full_access mode.
const HARD_BLOCKED_COMMAND_PATTERNS: &[&str] = &[
    "rm -rf /",
    "rm -rf /*",
    "rm -fr /",
    "rm -fr /*",
    "rm -rf ~",
    "rm -fr ~",
    "rm -rf ~/",
    "rm -fr ~/",
    "rm -rf .",
    "mkfs.",
    "diskutil erasedisk",
    "dd if=/dev/",
    ":(){ :|:& };:",
    "> /dev/sda",
    "> /dev/nvme",
    "chmod -r 000 /",
    "chmod -r 777 /",
    "chown -r ",
    "shutdown -",
    "reboot",
    "halt",
    "poweroff",
];

// ── Hard-protected path markers (ALWAYS enforced) ──
// Any command targeting these paths is refused outright.
const HARD_PROTECTED_PATH_MARKERS: &[&str] = &[
    "/.empyralis/state/vault",
    "/.empyralis/state",
    "/.ssh",
    "/.gnupg",
    "/etc/empyralis",
    "/var/lib/empyralis/agent-computer",
    "/.orion-stack",
];

fn hard_blocked_command(command: &str) -> bool {
    let compact = command
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_ascii_lowercase();
    if compact.is_empty() {
        return false;
    }
    let normalized = compact.split_whitespace().collect::<Vec<_>>().join(" ");
    for pattern in HARD_BLOCKED_COMMAND_PATTERNS {
        if let Some(pos) = normalized.find(pattern) {
            let after = normalized[pos + pattern.len()..].chars().next();
            // End-of-string or space: exact token boundary → always match
            if after.is_none() || after == Some(' ') {
                return true;
            }
            // Path-root boundary check: patterns like "rm -rf /", "rm -rf ~",
            // "rm -rf ~/" target a root/home path. They must NOT match when the
            // path is a subdirectory (e.g. "rm -rf /tmp/scratch" or
            // "rm -rf ~/workspace" — the agent CAN destroy its workspace scope).
            // Other patterns (like "dd if=/dev/" or "mkfs.") are prefixes that
            // SHOULD match longer forms.
            if after != Some(' ') {
                // Only apply boundary to rm-style patterns targeting a root path
                if pattern.starts_with("rm -") &&
                   (pattern.ends_with(" /") || pattern.ends_with(" ~") || pattern.ends_with(" ~/") || pattern.ends_with(" .")) {
                    continue;
                }
            }
            // For all other patterns (prefixes like "mkfs.", "shutdown -",
            // "dd if=/dev/"), continue matching even if followed by more chars.
            return true;
        }
    }
    false
}

fn command_touches_protected_path(command: &str) -> bool {
    for token in command.split_whitespace() {
        let cleaned = token.trim_matches('"').trim_matches('\'').to_ascii_lowercase();
        for marker in HARD_PROTECTED_PATH_MARKERS {
            if cleaned.contains(marker) {
                return true;
            }
        }
    }
    false
}

fn extract_shell_command(command_payload: &Value) -> Option<String> {
    // Navigate command_payload → arguments → command
    let arguments = command_payload.get("arguments")
        .or_else(|| command_payload.get("args"));
    if let Some(args) = arguments {
        if let Some(cmd) = args.get("command").and_then(Value::as_str) {
            let trimmed = cmd.trim();
            if !trimmed.is_empty() {
                return Some(trimmed.to_string());
            }
        }
        if let Some(cmd) = args.get("cmd").and_then(Value::as_str) {
            let trimmed = cmd.trim();
            if !trimmed.is_empty() {
                return Some(trimmed.to_string());
            }
        }
    }
    None
}

fn extract_filesystem_path(command_payload: &Value) -> Option<String> {
    let arguments = command_payload.get("arguments")
        .or_else(|| command_payload.get("args"));
    if let Some(args) = arguments {
        if let Some(path) = args.get("path").and_then(Value::as_str) {
            let trimmed = path.trim();
            if !trimmed.is_empty() {
                return Some(trimmed.to_string());
            }
        }
        if let Some(path) = args.get("file_path").and_then(Value::as_str) {
            let trimmed = path.trim();
            if !trimmed.is_empty() {
                return Some(trimmed.to_string());
            }
        }
    }
    None
}

fn execute_command_decision(
    input: &Value,
    workspace_id: Option<String>,
    worker_id: Option<String>,
    run_id: Option<String>,
    actor_role: &str,
) -> Value {
    if !can_read(actor_role) {
        return block("local_worker_actor_cannot_read");
    }
    if !boolish(input, "local_companion_enabled", true) {
        return block("local_companion_disabled");
    }
    if boolish(input, "kill_switch_active", false) {
        return block("local_worker_kill_switch_active");
    }
    let workspace = workspace_id.as_deref().unwrap_or("default");
    // Validate command_id is present (command must be traceable)
    if string_field(input, "command_id").is_none() {
        return block("command_id_missing");
    }

    // ── Hard-blocks: inspect command content for catastrophic/destructive ops ──
    // These checks are NEVER bypassed regardless of autonomy mode or trust level.
    let command_payload = input.get("command_payload").or_else(|| input.get("payload"));
    if let Some(payload) = command_payload {
        // Check shell commands for hard-blocked patterns
        if let Some(cmd) = extract_shell_command(payload) {
            if hard_blocked_command(&cmd) {
                return block("destructive_command_permanently_blocked");
            }
            if command_touches_protected_path(&cmd) {
                return block("protected_path_permanently_blocked");
            }
        }
        // Check filesystem paths for protected markers
        if let Some(path) = extract_filesystem_path(payload) {
            let cleaned = path.to_ascii_lowercase();
            for marker in HARD_PROTECTED_PATH_MARKERS {
                if cleaned.contains(marker) {
                    return block("protected_path_permanently_blocked");
                }
            }
        }
    }

    allow(
        "execute_command_allowed",
        "execute_command",
        "execute_hardware_command",
        Some(workspace.to_string()),
        worker_id,
        run_id,
        actor_role,
        false,
        "security",
    )
}

fn read_decision(
    reason: &str,
    operation: &str,
    next_action: &str,
    workspace_id: Option<String>,
    worker_id: Option<String>,
    run_id: Option<String>,
    actor_role: &str,
    cacheable: bool,
) -> Value {
    if !can_read(actor_role) {
        return block("local_worker_actor_cannot_read");
    }
    allow(
        reason,
        operation,
        next_action,
        workspace_id,
        worker_id,
        run_id,
        actor_role,
        cacheable,
        "standard",
    )
}

fn allow(
    reason: &str,
    operation: &str,
    next_action: &str,
    workspace_id: Option<String>,
    worker_id: Option<String>,
    run_id: Option<String>,
    actor_role: &str,
    cacheable: bool,
    audit_visibility: &str,
) -> Value {
    response(
        "allow",
        reason,
        operation,
        next_action,
        workspace_id,
        worker_id,
        run_id,
        actor_role,
        false,
        cacheable,
        audit_visibility,
    )
}

fn require_approval(
    reason: &str,
    operation: &str,
    next_action: &str,
    workspace_id: Option<String>,
    worker_id: Option<String>,
    run_id: Option<String>,
    actor_role: &str,
) -> Value {
    response(
        "require_approval",
        reason,
        operation,
        next_action,
        workspace_id,
        worker_id,
        run_id,
        actor_role,
        true,
        false,
        "security",
    )
}

fn block(reason: &str) -> Value {
    json!({
        "ok": false,
        "decision": "block",
        "reason": reason,
        "approval_required": false,
        "cacheable": false,
        "audit_visibility": "security",
    })
}

fn response(
    decision: &str,
    reason: &str,
    operation: &str,
    next_action: &str,
    workspace_id: Option<String>,
    worker_id: Option<String>,
    run_id: Option<String>,
    actor_role: &str,
    approval_required: bool,
    cacheable: bool,
    audit_visibility: &str,
) -> Value {
    json!({
        "ok": decision != "block",
        "decision": decision,
        "reason": reason,
        "operation": operation,
        "next_action": next_action,
        "workspace_id": workspace_id,
        "worker_id": worker_id,
        "run_id": run_id,
        "actor_role": actor_role,
        "approval_required": approval_required || decision == "require_approval",
        "cacheable": cacheable,
        "audit_visibility": audit_visibility,
    })
}

fn normalize_operation(value: &str) -> String {
    match normalize_token(value).as_str() {
        "get_queue" | "queue" | "local_run_queue" => "get_queue".to_string(),
        "cleanup_queue" | "cleanup_local_queue" => "cleanup_queue".to_string(),
        "worker_status" | "workers_status" => "worker_status".to_string(),
        "worker_heartbeat" | "heartbeat_worker" => "worker_heartbeat".to_string(),
        "runtime_heartbeat" | "heartbeat_runtime" => "runtime_heartbeat".to_string(),
        "claim_run" | "claim_local_run" => "claim_run".to_string(),
        "run_heartbeat" | "heartbeat_run" => "run_heartbeat".to_string(),
        "control_state" | "run_control_state" => "control_state".to_string(),
        "complete_run" | "complete_local_run" => "complete_run".to_string(),
        "pause_run" | "pause_local_run" => "pause_run".to_string(),
        "fail_run" | "fail_local_run" => "fail_run".to_string(),
        "execute_command" | "exec_command" | "execute_hardware_command" => "execute_command".to_string(),
        _ => String::new(),
    }
}

fn normalize_actor_role(value: &str) -> String {
    match normalize_token(value).as_str() {
        "system" | "service" => "system".to_string(),
        "owner" | "admin" => "owner".to_string(),
        "member" | "worker" | "runtime" => "worker".to_string(),
        "viewer" | "readonly" | "read_only" => "viewer".to_string(),
        _ => "worker".to_string(),
    }
}

fn normalize_token(value: &str) -> String {
    value
        .trim()
        .to_ascii_lowercase()
        .replace('-', "_")
        .replace('.', "_")
        .replace(' ', "_")
}

fn string_field(input: &Value, key: &str) -> Option<String> {
    input.get(key).and_then(Value::as_str).and_then(|value| {
        let trimmed = value.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_string())
        }
    })
}

fn boolish(input: &Value, key: &str, default_value: bool) -> bool {
    if input.get(key).is_none() {
        return default_value;
    }
    match input.get(key) {
        Some(Value::Bool(value)) => *value,
        Some(Value::String(value)) => matches!(
            normalize_token(value).as_str(),
            "1" | "true" | "yes" | "on" | "enabled" | "active" | "present"
        ),
        Some(Value::Number(value)) => value.as_i64().unwrap_or(0) > 0,
        _ => false,
    }
}

fn positive_i64(input: &Value, key: &str, default_value: i64) -> i64 {
    if input.get(key).is_none() {
        return default_value;
    }
    match input.get(key) {
        Some(Value::Number(value)) => value.as_i64().unwrap_or(default_value),
        Some(Value::String(value)) => value.trim().parse::<i64>().unwrap_or(default_value),
        _ => default_value,
    }
}

fn worker_mismatch(worker_id: &Option<String>, current_worker_id: &Option<String>) -> bool {
    match (worker_id.as_ref(), current_worker_id.as_ref()) {
        (Some(requester), Some(current)) => requester != current,
        _ => false,
    }
}

fn requires_workspace(operation: &str) -> bool {
    matches!(operation, "get_queue" | "cleanup_queue" | "execute_command")
}

fn requires_worker(operation: &str) -> bool {
    matches!(
        operation,
        "claim_run"
            | "worker_heartbeat"
            | "runtime_heartbeat"
            | "run_heartbeat"
            | "control_state"
            | "complete_run"
            | "pause_run"
            | "fail_run"
    )
}

fn requires_run(operation: &str) -> bool {
    matches!(
        operation,
        "run_heartbeat" | "control_state" | "complete_run" | "pause_run" | "fail_run"
    )
}

fn mutates_queue(operation: &str) -> bool {
    !matches!(operation, "get_queue" | "worker_status" | "control_state")
}

fn can_read(actor_role: &str) -> bool {
    matches!(actor_role, "system" | "owner" | "worker" | "viewer")
}

fn can_admin(actor_role: &str) -> bool {
    matches!(actor_role, "system" | "owner")
}

fn run_is_active(run_status: &str) -> bool {
    run_status.is_empty()
        || matches!(
            run_status,
            "queued_local" | "running_local" | "running" | "starting" | "waiting_for_input"
        )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blocks_missing_operation() {
        let response = local_worker_decision_command(&json!({"workspace_id": "w1"}));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "local_worker_operation_missing");
    }

    #[test]
    fn get_queue_validates_limit() {
        let response = local_worker_decision_command(&json!({
            "operation": "get_queue",
            "workspace_id": "w1",
            "limit": 500
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "local_queue_limit_invalid");
    }

    #[test]
    fn cleanup_without_confirmation_requires_approval() {
        let response = local_worker_decision_command(&json!({
            "operation": "cleanup_queue",
            "workspace_id": "w1",
            "actor_role": "owner",
            "dry_run": false
        }));
        assert_eq!(response["decision"], "require_approval");
        assert_eq!(response["next_action"], "request_cleanup_confirmation");
    }

    #[test]
    fn claim_blocks_when_companion_disabled() {
        let response = local_worker_decision_command(&json!({
            "operation": "claim_run",
            "local_companion_enabled": false
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "local_companion_disabled");
    }

    #[test]
    fn complete_requires_active_state() {
        let response = local_worker_decision_command(&json!({
            "operation": "complete_run",
            "worker_id": "worker1",
            "run_id": "run1",
            "run_status": "completed"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "local_run_complete_state_invalid");
    }

    #[test]
    fn fail_requires_error() {
        let response = local_worker_decision_command(&json!({
            "operation": "fail_run",
            "worker_id": "worker1",
            "run_id": "run1",
            "run_status": "running_local"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "local_run_error_missing");
    }

    #[test]
    fn pause_blocks_mismatched_worker() {
        let response = local_worker_decision_command(&json!({
            "operation": "pause_run",
            "worker_id": "worker-2",
            "current_worker_id": "worker-1",
            "run_id": "run1",
            "run_status": "running_local",
            "wait_reason": "human_required"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "local_run_not_owned_by_worker");
    }

    #[test]
    fn run_heartbeat_blocks_missing_claim() {
        let response = local_worker_decision_command(&json!({
            "operation": "run_heartbeat",
            "worker_id": "worker-1",
            "run_id": "run1",
            "run_status": "running_local",
            "claim_missing": true
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "local_run_claim_missing");
    }

    #[test]
    fn complete_blocks_missing_claim() {
        let response = local_worker_decision_command(&json!({
            "operation": "complete_run",
            "worker_id": "worker-1",
            "run_id": "run1",
            "run_status": "running_local",
            "claim_missing": true,
            "result_text": "done"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "local_run_claim_missing");
    }

    #[test]
    fn pause_blocks_missing_claim() {
        let response = local_worker_decision_command(&json!({
            "operation": "pause_run",
            "worker_id": "worker-1",
            "run_id": "run1",
            "run_status": "running_local",
            "claim_missing": true,
            "wait_reason": "human_required"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "local_run_claim_missing");
    }

    #[test]
    fn fail_blocks_missing_claim() {
        let response = local_worker_decision_command(&json!({
            "operation": "fail_run",
            "worker_id": "worker-1",
            "run_id": "run1",
            "run_status": "running_local",
            "claim_missing": true,
            "error": "boom"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "local_run_claim_missing");
    }
}

#[cfg(test)]
mod local_worker_kernel_boundary_tests {
    use super::local_worker_decision_command;

    #[test]
    fn claim_run_requires_worker_identity() {
        let decision = local_worker_decision_command(&serde_json::json!({
            "operation": "claim_run",
            "workspace_id": "default",
            "run_id": "run-1"
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("block")
        );
        assert_eq!(
            decision.get("reason").and_then(|value| value.as_str()),
            Some("worker_id_missing")
        );
    }

    #[test]
    fn local_companion_disabled_blocks_queue_mutation() {
        let decision = local_worker_decision_command(&serde_json::json!({
            "operation": "complete_run",
            "workspace_id": "default",
            "worker_id": "worker-1",
            "run_id": "run-1",
            "run_status": "claimed",
            "local_companion_enabled": false
        }));

        assert_eq!(
            decision.get("decision").and_then(|value| value.as_str()),
            Some("block")
        );
        assert_eq!(
            decision.get("reason").and_then(|value| value.as_str()),
            Some("local_companion_disabled")
        );
    }
}

#[cfg(test)]
mod command_safety_tests {
    use super::{hard_blocked_command, command_touches_protected_path,
                extract_shell_command, extract_filesystem_path};

    #[test]
    fn hard_blocked_command_catches_rm_rf_root() {
        assert!(hard_blocked_command("rm -rf /"));
        assert!(hard_blocked_command("rm -rf /*"));
        assert!(hard_blocked_command("rm -fr /"));
        assert!(hard_blocked_command("rm -rf ~"));
        assert!(hard_blocked_command("rm -rf ~/"));
        // Normal commands pass
        assert!(!hard_blocked_command("ls -la /tmp"));
        assert!(!hard_blocked_command("python3 script.py"));
    }

    #[test]
    fn hard_blocked_command_allows_workspace_deletion() {
        // Agent CAN destroy its workspace scope (rebuildable scratch)
        assert!(!hard_blocked_command("rm -rf ~/workspace"));
        assert!(!hard_blocked_command("rm -rf ~/workspace/output"));
        assert!(!hard_blocked_command("rm -rf /tmp/scratch"));
    }

    #[test]
    fn hard_blocked_command_catches_catastrophic_commands() {
        for cmd in &[
            "mkfs.ext4 /dev/sda",
            "diskutil eraseDisk JHFS+ Empyralis /dev/disk0",
            "dd if=/dev/zero of=/dev/sda",
            ":(){ :|:& };:",
            "shutdown -h now",
            "reboot",
            "halt",
            "poweroff",
        ] {
            assert!(hard_blocked_command(cmd), "expected blocked: {cmd}");
        }
    }

    #[test]
    fn command_touches_protected_path_catches_vault_and_ssh() {
        assert!(command_touches_protected_path("rm -rf ~/.empyralis/state/vault"));
        assert!(command_touches_protected_path("rm -rf ~/.empyralis/state/vault/credentials.json"));
        assert!(command_touches_protected_path("cat ~/.empyralis/state/vault/key"));
        assert!(command_touches_protected_path("rm -rf ~/.ssh"));
        assert!(command_touches_protected_path("rm ~/.ssh/id_rsa"));
        assert!(command_touches_protected_path("rm /etc/empyralis/agent-computer.env"));
        assert!(command_touches_protected_path("rm -rf /var/lib/empyralis/agent-computer"));
        // Normal paths pass
        assert!(!command_touches_protected_path("rm -rf /tmp/scratch"));
        assert!(!command_touches_protected_path("rm ~/workspace/output.txt"));
    }

    #[test]
    fn extract_shell_command_from_payload() {
        let payload = serde_json::json!({
            "capability_id": "shell.execute",
            "arguments": {
                "command": "rm -rf /",
                "timeout_seconds": 30
            }
        });
        assert_eq!(extract_shell_command(&payload), Some("rm -rf /".to_string()));
    }

    #[test]
    fn extract_filesystem_path_from_payload() {
        let payload = serde_json::json!({
            "capability_id": "filesystem.write",
            "arguments": {
                "path": "~/.empyralis/state/vault/credentials.json",
                "content": "malicious"
            }
        });
        assert_eq!(
            extract_filesystem_path(&payload),
            Some("~/.empyralis/state/vault/credentials.json".to_string())
        );
    }

    // ── Integration: kernel blocks destructive commands ──

    #[test]
    fn execute_command_decision_blocks_rm_rf_root() {
        let decision = super::local_worker_decision_command(&serde_json::json!({
            "operation": "execute_command",
            "workspace_id": "w1",
            "command_id": "cmd-1",
            "command_payload": {
                "capability_id": "shell.execute",
                "arguments": {
                    "command": "rm -rf /"
                }
            }
        }));
        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "destructive_command_permanently_blocked");
    }

    #[test]
    fn execute_command_decision_blocks_vault_rm() {
        // "rm -rf ~/.empyralis/state/vault" is caught by command_touches_protected_path
        // (NOT by hard_blocked_command because "rm -rf ~" no longer matches "rm -rf ~/something")
        let decision = super::local_worker_decision_command(&serde_json::json!({
            "operation": "execute_command",
            "workspace_id": "w1",
            "command_id": "cmd-2",
            "command_payload": {
                "capability_id": "shell.execute",
                "arguments": {
                    "command": "rm -rf ~/.empyralis/state/vault"
                }
            }
        }));
        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "protected_path_permanently_blocked");
    }

    #[test]
    fn execute_command_decision_blocks_filesystem_write_to_vault() {
        let decision = super::local_worker_decision_command(&serde_json::json!({
            "operation": "execute_command",
            "workspace_id": "w1",
            "command_id": "cmd-3",
            "command_payload": {
                "capability_id": "filesystem.write",
                "arguments": {
                    "path": "~/.empyralis/state/vault/evil.json",
                    "content": "pwned"
                }
            }
        }));
        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "protected_path_permanently_blocked");
    }

    #[test]
    fn execute_command_decision_allows_normal_commands() {
        let decision = super::local_worker_decision_command(&serde_json::json!({
            "operation": "execute_command",
            "workspace_id": "w1",
            "command_id": "cmd-4",
            "command_payload": {
                "capability_id": "shell.execute",
                "arguments": {
                    "command": "ls -la /tmp"
                }
            }
        }));
        assert_eq!(decision["decision"], "allow");
    }

    #[test]
    fn execute_command_decision_allows_normal_filesystem_ops() {
        let decision = super::local_worker_decision_command(&serde_json::json!({
            "operation": "execute_command",
            "workspace_id": "w1",
            "command_id": "cmd-5",
            "command_payload": {
                "capability_id": "filesystem.write",
                "arguments": {
                    "path": "~/workspace/output.txt",
                    "content": "hello"
                }
            }
        }));
        assert_eq!(decision["decision"], "allow");
    }

    #[test]
    fn execute_command_decision_allows_workspace_deletion() {
        // The agent CAN destroy its workspace scope — that's rebuildable scratch
        let decision = super::local_worker_decision_command(&serde_json::json!({
            "operation": "execute_command",
            "workspace_id": "w1",
            "command_id": "cmd-6",
            "command_payload": {
                "capability_id": "shell.execute",
                "arguments": {
                    "command": "rm -rf ~/workspace/build"
                }
            }
        }));
        assert_eq!(decision["decision"], "allow");
    }
}

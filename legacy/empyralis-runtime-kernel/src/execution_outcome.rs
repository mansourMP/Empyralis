use crate::policy::normalize_token;
use crate::protocol;
use serde_json::{json, Value};

const DEFAULT_PREVIEW_BYTES: usize = 2_000;
const MAX_PREVIEW_BYTES: usize = 20_000;
const RETRYABLE_EXIT_CODES: &[i64] = &[1, 2, 7, 28, 35, 124, 137, 143];
const TRANSIENT_MARKERS: &[&str] = &[
    "connection reset",
    "connection refused",
    "timed out",
    "timeout",
    "temporary failure",
    "try again",
    "rate limit",
    "too many requests",
    "eai_again",
];
const SECRET_MARKERS: &[&str] = &[
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

pub fn execution_outcome_command(input: &Value) -> Value {
    let timed_out = boolish(input, "timed_out") || status_is(input, "timeout");
    let cancelled = boolish(input, "cancelled") || status_is(input, "cancelled");
    let exit_code = input.get("exit_code").and_then(Value::as_i64);
    let signal = protocol::string_field(input, "signal");

    if exit_code.is_none() && !timed_out && !cancelled && signal.is_none() {
        return protocol::block("execution_outcome_missing");
    }

    let stdout = protocol::string_field(input, "stdout").unwrap_or_default();
    let stderr = protocol::string_field(input, "stderr").unwrap_or_default();
    let stdout_bytes = input
        .get("stdout_bytes")
        .and_then(Value::as_u64)
        .unwrap_or(stdout.len() as u64);
    let stderr_bytes = input
        .get("stderr_bytes")
        .and_then(Value::as_u64)
        .unwrap_or(stderr.len() as u64);
    let max_preview_bytes = input
        .get("max_preview_bytes")
        .and_then(Value::as_u64)
        .unwrap_or(DEFAULT_PREVIEW_BYTES as u64)
        .clamp(1, MAX_PREVIEW_BYTES as u64) as usize;
    let output_truncated = boolish(input, "output_truncated")
        || stdout_bytes.saturating_add(stderr_bytes) > max_preview_bytes as u64;
    let artifact_count = input
        .get("artifacts")
        .and_then(Value::as_array)
        .map(Vec::len)
        .unwrap_or(0);

    let stdout_secret = contains_secret_marker(&stdout);
    let stderr_secret = contains_secret_marker(&stderr);
    let secret_detected = stdout_secret || stderr_secret;
    let status = outcome_status(exit_code, timed_out, cancelled, signal.as_deref());
    let retryable = retryable(&status, exit_code, &stderr);
    let retention_class =
        retention_class(&status, output_truncated, secret_detected, artifact_count);
    let audit_visibility = audit_visibility(&status, output_truncated, secret_detected);
    let final_status = final_status(&status);
    let stdout_preview = preview(&redact_text(&stdout), max_preview_bytes);
    let stderr_preview = preview(&redact_text(&stderr), max_preview_bytes);
    let summary = summary(&status, &stdout_preview, &stderr_preview);
    let event = event(&status);
    let log_level = log_level(&status);

    json!({
        "ok": true,
        "decision": "allow",
        "reason": "execution_outcome_normalized",
        "status": status,
        "final_status": final_status,
        "summary": summary,
        "event": event,
        "log_level": log_level,
        "record_patch": {
            "result": summary,
            "status": final_status,
            "event": event,
            "log_level": log_level,
            "execution_outcome": {
                "status": status,
                "final_status": final_status,
                "summary": summary,
                "event": event,
                "log_level": log_level,
                "retryable": retryable,
                "retention_class": retention_class,
                "audit_visibility": audit_visibility,
                "output": {
                    "stdout_bytes": stdout_bytes,
                    "stderr_bytes": stderr_bytes,
                    "truncated": output_truncated,
                    "secret_detected": secret_detected,
                    "stdout_preview": stdout_preview,
                    "stderr_preview": stderr_preview,
                },
                "artifacts": {
                    "count": artifact_count,
                    "present": artifact_count > 0,
                },
            }
        },
        "exit_code": exit_code,
        "signal": signal,
        "timed_out": timed_out,
        "cancelled": cancelled,
        "retryable": retryable,
        "output": {
            "stdout_bytes": stdout_bytes,
            "stderr_bytes": stderr_bytes,
            "truncated": output_truncated,
            "secret_detected": secret_detected,
            "stdout_preview": stdout_preview,
            "stderr_preview": stderr_preview,
        },
        "artifacts": {
            "count": artifact_count,
            "present": artifact_count > 0,
        },
        "retention_class": retention_class,
        "audit_visibility": audit_visibility,
        "cacheable": false,
    })
}

fn outcome_status(
    exit_code: Option<i64>,
    timed_out: bool,
    cancelled: bool,
    signal: Option<&str>,
) -> &'static str {
    if timed_out {
        "timed_out"
    } else if cancelled {
        "cancelled"
    } else if signal
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .is_some()
    {
        "signaled"
    } else if exit_code == Some(0) {
        "succeeded"
    } else {
        "failed"
    }
}

fn retryable(status: &str, exit_code: Option<i64>, stderr: &str) -> bool {
    if matches!(status, "succeeded" | "cancelled") {
        return false;
    }
    if matches!(status, "timed_out" | "signaled") {
        return true;
    }
    if exit_code
        .map(|code| RETRYABLE_EXIT_CODES.contains(&code))
        .unwrap_or(false)
    {
        return true;
    }
    let lower = stderr.to_ascii_lowercase();
    TRANSIENT_MARKERS
        .iter()
        .any(|marker| lower.contains(marker))
}

fn retention_class(
    status: &str,
    output_truncated: bool,
    secret_detected: bool,
    artifact_count: usize,
) -> &'static str {
    if secret_detected || matches!(status, "timed_out" | "signaled") {
        "extended"
    } else if output_truncated || artifact_count > 0 || status == "failed" {
        "standard"
    } else {
        "short"
    }
}

fn audit_visibility(status: &str, output_truncated: bool, secret_detected: bool) -> &'static str {
    if secret_detected || output_truncated || !matches!(status, "succeeded") {
        "security"
    } else {
        "standard"
    }
}

fn final_status(status: &str) -> &'static str {
    match status {
        "succeeded" => "completed",
        "timed_out" => "timeout",
        "cancelled" => "cancelled",
        "signaled" | "failed" => "failed",
        _ => "failed",
    }
}

fn summary<'a>(status: &str, stdout_preview: &'a str, stderr_preview: &'a str) -> String {
    match status {
        "succeeded" => {
            if !stdout_preview.trim().is_empty() {
                stdout_preview.to_string()
            } else {
                "Execution succeeded.".to_string()
            }
        }
        "timed_out" => {
            if !stderr_preview.trim().is_empty() {
                stderr_preview.to_string()
            } else {
                "Execution timed out.".to_string()
            }
        }
        "cancelled" => {
            if !stderr_preview.trim().is_empty() {
                stderr_preview.to_string()
            } else {
                "Execution cancelled.".to_string()
            }
        }
        "signaled" | "failed" => {
            if !stderr_preview.trim().is_empty() {
                stderr_preview.to_string()
            } else if !stdout_preview.trim().is_empty() {
                stdout_preview.to_string()
            } else {
                "Execution failed.".to_string()
            }
        }
        _ => "Execution finished.".to_string(),
    }
}

fn event(status: &str) -> &'static str {
    match status {
        "succeeded" => "run_complete",
        "timed_out" => "timeout",
        "cancelled" => "run_stopped",
        "signaled" | "failed" => "run_error",
        _ => "run_error",
    }
}

fn log_level(status: &str) -> &'static str {
    match status {
        "succeeded" => "info",
        "cancelled" => "warn",
        "timed_out" | "signaled" | "failed" => "error",
        _ => "error",
    }
}

fn boolish(input: &Value, key: &str) -> bool {
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

fn status_is(input: &Value, expected: &str) -> bool {
    protocol::string_field(input, "status")
        .map(|value| normalize_token(&value) == expected)
        .unwrap_or(false)
}

fn contains_secret_marker(value: &str) -> bool {
    let lower = value.to_ascii_lowercase();
    SECRET_MARKERS.iter().any(|marker| lower.contains(marker))
}

fn redact_text(value: &str) -> String {
    if !contains_secret_marker(value) {
        return value.to_string();
    }
    value
        .split_whitespace()
        .map(|token| {
            if contains_secret_marker(token) {
                redact_token(token)
            } else {
                token.to_string()
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn redact_token(value: &str) -> String {
    if value.len() <= 8 {
        "***REDACTED***".to_string()
    } else {
        let prefix = value.chars().take(4).collect::<String>();
        format!("{prefix}...REDACTED")
    }
}

fn preview(value: &str, max_bytes: usize) -> String {
    if value.len() <= max_bytes {
        return value.to_string();
    }
    let mut end = 0;
    for (index, _) in value.char_indices() {
        if index > max_bytes {
            break;
        }
        end = index;
    }
    format!("{}...TRUNCATED", &value[..end])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_outcome_blocks() {
        let response = execution_outcome_command(&json!({}));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "execution_outcome_missing");
    }

    #[test]
    fn zero_exit_succeeds() {
        let response = execution_outcome_command(&json!({
            "exit_code": 0,
            "stdout": "done"
        }));
        assert_eq!(response["status"], "succeeded");
        assert_eq!(response["final_status"], "completed");
        assert_eq!(response["event"], "run_complete");
        assert_eq!(response["log_level"], "info");
        assert_eq!(response["record_patch"]["result"], "done");
        assert_eq!(response["record_patch"]["status"], "completed");
        assert_eq!(
            response["record_patch"]["execution_outcome"]["final_status"],
            "completed"
        );
        assert_eq!(response["retryable"], false);
        assert_eq!(response["retention_class"], "short");
    }

    #[test]
    fn timeout_is_retryable_and_extended() {
        let response = execution_outcome_command(&json!({
            "timed_out": true,
            "stderr": "timed out"
        }));
        assert_eq!(response["status"], "timed_out");
        assert_eq!(response["final_status"], "timeout");
        assert_eq!(response["event"], "timeout");
        assert_eq!(response["log_level"], "error");
        assert_eq!(response["record_patch"]["status"], "timeout");
        assert_eq!(
            response["record_patch"]["execution_outcome"]["retryable"],
            true
        );
        assert_eq!(response["retryable"], true);
        assert_eq!(response["retention_class"], "extended");
    }

    #[test]
    fn transient_failure_is_retryable() {
        let response = execution_outcome_command(&json!({
            "exit_code": 1,
            "stderr": "connection reset by peer"
        }));
        assert_eq!(response["status"], "failed");
        assert_eq!(response["retryable"], true);
    }

    #[test]
    fn secret_output_is_redacted() {
        let response = execution_outcome_command(&json!({
            "exit_code": 0,
            "stdout": "token sk-test-secret-value"
        }));
        assert_eq!(response["output"]["secret_detected"], true);
        assert_eq!(
            response["output"]["stdout_preview"],
            "token sk-t...REDACTED"
        );
        assert_eq!(response["summary"], "token sk-t...REDACTED");
        assert_eq!(response["audit_visibility"], "security");
    }
}

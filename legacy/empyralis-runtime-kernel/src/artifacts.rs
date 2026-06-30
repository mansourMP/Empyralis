use crate::policy::normalize_token;
use crate::protocol;
use serde_json::{json, Value};

const DEFAULT_MAX_ARTIFACT_BYTES: u64 = 100 * 1024 * 1024;
const SECRET_NAME_MARKERS: &[&str] = &[
    ".env",
    ".pem",
    ".key",
    ".p12",
    "id_rsa",
    "kubeconfig",
    "credential",
    "credentials",
    "secret",
    "token",
    "password",
    "private_key",
];
const EXECUTABLE_EXTENSIONS: &[&str] = &[
    ".app", ".bat", ".bin", ".cmd", ".com", ".dll", ".dylib", ".exe", ".msi", ".scr", ".sh",
];
const ARCHIVE_EXTENSIONS: &[&str] = &[".7z", ".gz", ".rar", ".tar", ".tgz", ".zip"];

pub fn artifact_policy_command(input: &Value) -> Value {
    let action = normalize_action(
        protocol::string_field(input, "action")
            .or_else(|| protocol::string_field(input, "operation"))
            .as_deref()
            .unwrap_or(""),
    );
    if action.is_empty() {
        return artifact_response(
            "block",
            "artifact_action_missing",
            "",
            &protocol::string_field(input, "path")
                .or_else(|| protocol::string_field(input, "artifact_path"))
                .or_else(|| protocol::string_field(input, "filename"))
                .unwrap_or_default(),
            None,
            false,
            false,
            DEFAULT_MAX_ARTIFACT_BYTES,
            None,
        );
    }

    let Some(path) = protocol::string_field(input, "path")
        .or_else(|| protocol::string_field(input, "artifact_path"))
        .or_else(|| protocol::string_field(input, "filename"))
    else {
        return artifact_response(
            "block",
            "artifact_path_missing",
            &action,
            "",
            None,
            false,
            false,
            DEFAULT_MAX_ARTIFACT_BYTES,
            None,
        );
    };

    if unsafe_path(&path) {
        return artifact_response(
            "block",
            "artifact_path_unsafe",
            &action,
            &path,
            None,
            false,
            false,
            0,
            None,
        );
    }

    let size_bytes = input.get("size_bytes").and_then(Value::as_u64).unwrap_or(0);
    let max_bytes = input
        .get("max_artifact_bytes")
        .and_then(Value::as_u64)
        .unwrap_or(DEFAULT_MAX_ARTIFACT_BYTES)
        .max(1);
    if size_bytes > max_bytes {
        return artifact_response(
            "block",
            "artifact_size_exceeds_limit",
            &action,
            &path,
            Some(size_bytes),
            false,
            false,
            max_bytes,
            None,
        );
    }

    let content_type = protocol::string_field(input, "content_type")
        .or_else(|| protocol::string_field(input, "mime_type"))
        .unwrap_or_else(|| infer_content_type(&path));
    let executable = boolish(input, "executable") || executable_path(&path);
    let archive = archive_path(&path);
    let secret_like = boolish(input, "secret_detected")
        || secret_like_path(&path)
        || secret_like_content_type(&content_type);

    let (decision, reason) = match action.as_str() {
        "register" if secret_like || executable => (
            "require_approval",
            "sensitive_artifact_register_requires_approval",
        ),
        "register" => ("allow", "artifact_register_allowed"),
        "read" if secret_like => (
            "require_approval",
            "sensitive_artifact_read_requires_approval",
        ),
        "read" => ("allow", "artifact_read_allowed"),
        "export" if secret_like => ("block", "sensitive_artifact_export_blocked"),
        "export" if executable => (
            "require_approval",
            "executable_artifact_export_requires_approval",
        ),
        "export" => ("allow", "artifact_export_allowed"),
        "delete" => ("require_approval", "artifact_delete_requires_approval"),
        _ => ("block", "unknown_artifact_action"),
    };

    artifact_response(
        decision,
        reason,
        &action,
        &path,
        Some(size_bytes),
        secret_like,
        executable,
        max_bytes,
        Some(json!({
            "content_type": content_type,
            "archive": archive,
            "retention_class": retention_class(decision, secret_like, executable, archive),
            "preview_allowed": decision != "block" && !secret_like && !executable,
            "external_export_allowed": decision == "allow" && action == "export",
        })),
    )
}

fn artifact_response(
    decision: &str,
    reason: &str,
    action: &str,
    path: &str,
    size_bytes: Option<u64>,
    secret_like: bool,
    executable: bool,
    max_bytes: u64,
    details: Option<Value>,
) -> Value {
    let next_action = match (action, decision) {
        ("register", "allow") => "register_artifact",
        ("register", "require_approval") => "request_artifact_registration_approval",
        ("read", "allow") => "read_artifact",
        ("read", "require_approval") => "request_artifact_read_approval",
        ("export", "allow") => "export_artifact",
        ("export", "require_approval") => "request_artifact_export_approval",
        ("delete", "require_approval") => "request_artifact_delete_approval",
        _ => "deny_artifact_operation",
    };
    json!({
        "ok": decision != "block",
        "decision": decision,
        "reason": reason,
        "next_action": next_action,
        "action": action,
        "path": path,
        "size_bytes": size_bytes,
        "max_artifact_bytes": max_bytes,
        "secret_like": secret_like,
        "executable": executable,
        "approval_required": decision == "require_approval",
        "artifact": details,
        "audit_visibility": if decision == "allow" && !secret_like && !executable { "standard" } else { "security" },
        "cacheable": false,
    })
}

fn normalize_action(value: &str) -> String {
    match normalize_token(value).as_str() {
        "register" | "create" | "write" | "store" => "register".to_string(),
        "read" | "preview" | "open" => "read".to_string(),
        "export" | "download" | "send" | "share" => "export".to_string(),
        "delete" | "remove" => "delete".to_string(),
        _ => String::new(),
    }
}

fn unsafe_path(value: &str) -> bool {
    let token = value.trim();
    token.is_empty()
        || token.contains('\0')
        || token.contains("../")
        || token == ".."
        || token.starts_with("/etc/")
        || token.starts_with("/var/")
        || token.starts_with("/private/")
        || token.starts_with("/System/")
}

fn infer_content_type(path: &str) -> String {
    let lower = path.to_ascii_lowercase();
    if lower.ends_with(".png") {
        "image/png".to_string()
    } else if lower.ends_with(".jpg") || lower.ends_with(".jpeg") {
        "image/jpeg".to_string()
    } else if lower.ends_with(".json") {
        "application/json".to_string()
    } else if lower.ends_with(".txt") || lower.ends_with(".log") || lower.ends_with(".md") {
        "text/plain".to_string()
    } else if archive_path(path) {
        "application/archive".to_string()
    } else {
        "application/octet-stream".to_string()
    }
}

fn secret_like_path(path: &str) -> bool {
    let lower = path.to_ascii_lowercase();
    SECRET_NAME_MARKERS
        .iter()
        .any(|marker| lower.contains(marker))
}

fn secret_like_content_type(content_type: &str) -> bool {
    let normalized = content_type.to_ascii_lowercase();
    normalized.contains("private-key")
        || normalized.contains("credential")
        || normalized.contains("secret")
}

fn executable_path(path: &str) -> bool {
    let lower = path.to_ascii_lowercase();
    EXECUTABLE_EXTENSIONS
        .iter()
        .any(|extension| lower.ends_with(extension))
}

fn archive_path(path: &str) -> bool {
    let lower = path.to_ascii_lowercase();
    ARCHIVE_EXTENSIONS
        .iter()
        .any(|extension| lower.ends_with(extension))
}

fn retention_class(
    decision: &str,
    secret_like: bool,
    executable: bool,
    archive: bool,
) -> &'static str {
    if decision == "block" || secret_like {
        "extended"
    } else if executable || archive {
        "standard"
    } else {
        "short"
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_action_blocks() {
        let response = artifact_policy_command(&json!({"path": "out.txt"}));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "artifact_action_missing");
        assert_eq!(response["next_action"], "deny_artifact_operation");
    }

    #[test]
    fn unsafe_path_blocks() {
        let response = artifact_policy_command(&json!({
            "action": "register",
            "path": "../secret.txt"
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "artifact_path_unsafe");
        assert_eq!(response["next_action"], "deny_artifact_operation");
    }

    #[test]
    fn normal_register_allows() {
        let response = artifact_policy_command(&json!({
            "action": "register",
            "path": "outputs/report.txt",
            "size_bytes": 128
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_action"], "register_artifact");
        assert_eq!(response["artifact"]["retention_class"], "short");
        assert_eq!(response["artifact"]["preview_allowed"], true);
    }

    #[test]
    fn secret_export_blocks() {
        let response = artifact_policy_command(&json!({
            "action": "export",
            "path": "outputs/.env",
            "size_bytes": 100
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["next_action"], "deny_artifact_operation");
        assert_eq!(response["reason"], "sensitive_artifact_export_blocked");
        assert_eq!(response["secret_like"], true);
    }

    #[test]
    fn executable_export_requires_approval() {
        let response = artifact_policy_command(&json!({
            "action": "export",
            "path": "outputs/tool.sh",
            "size_bytes": 100
        }));
        assert_eq!(response["decision"], "require_approval");
        assert_eq!(response["next_action"], "request_artifact_export_approval");
        assert_eq!(response["approval_required"], true);
    }

    #[test]
    fn oversize_artifact_blocks() {
        let response = artifact_policy_command(&json!({
            "action": "register",
            "path": "outputs/huge.bin",
            "size_bytes": 200,
            "max_artifact_bytes": 100
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "artifact_size_exceeds_limit");
        assert_eq!(response["next_action"], "deny_artifact_operation");
    }
}

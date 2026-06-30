use crate::protocol;
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};

pub fn build_sandbox_command(input: &Value) -> Value {
    let Some(sandbox_root) = protocol::string_field(input, "sandbox_root") else {
        return protocol::block("sandbox_root_missing");
    };
    let sandbox_root = match canonical_sandbox_root(&PathBuf::from(&sandbox_root)) {
        Ok(path) => path,
        Err(reason) => return protocol::block(&reason),
    };

    let image = protocol::string_field(input, "image")
        .unwrap_or_else(|| "empyralis-sandbox:latest".to_string());
    if input
        .get("network_enabled")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        return protocol::block("network_enabled_requires_sandbox_execution_policy");
    }
    if !input
        .get("read_only")
        .and_then(Value::as_bool)
        .unwrap_or(true)
    {
        return protocol::block("writable_base_image_requires_sandbox_execution_policy");
    }
    let memory_mb = input
        .get("memory_mb")
        .and_then(Value::as_u64)
        .unwrap_or(512)
        .max(32);
    let cpu_shares = input
        .get("cpu_shares")
        .and_then(Value::as_u64)
        .unwrap_or(1024)
        .max(2);
    let timeout_seconds = input
        .get("timeout_seconds")
        .and_then(Value::as_u64)
        .unwrap_or(25)
        .max(1);
    let uid = input.get("uid").and_then(Value::as_u64).unwrap_or(1000);
    let gid = input.get("gid").and_then(Value::as_u64).unwrap_or(1000);
    let worker_module = protocol::string_field(input, "worker_module")
        .unwrap_or_else(|| "server_modules.hosted_secure_worker".to_string());
    let output_file = protocol::string_field(input, "output_file")
        .unwrap_or_else(|| "/workspace/outputs/turn-result.json".to_string());

    let command = vec![
        "docker".to_string(),
        "run".to_string(),
        "--rm".to_string(),
        format!("--memory={memory_mb}m"),
        format!("--cpu-shares={cpu_shares}"),
        format!("--stop-timeout={timeout_seconds}"),
        format!("--user={uid}:{gid}"),
        format!("-v={}:/workspace:rw", sandbox_root.display()),
        "-w=/workspace".to_string(),
        "-e".to_string(),
        "PYTHONPATH=/app".to_string(),
        "-e".to_string(),
        format!("EMPYRALIS_SANDBOX_OUTPUT_FILE={output_file}"),
        "--read-only".to_string(),
        "--tmpfs=/tmp:rw,noexec,nosuid,size=256m".to_string(),
        "--tmpfs=/home/sandbox:rw,noexec,nosuid,size=128m".to_string(),
        "--network".to_string(),
        "none".to_string(),
        "--cap-drop".to_string(),
        "ALL".to_string(),
        "--security-opt".to_string(),
        "no-new-privileges:true".to_string(),
        image,
        "python3".to_string(),
        "-m".to_string(),
        worker_module,
    ];

    json!({
        "ok": true,
        "decision": "allow",
        "reason": "sandbox_command_built",
        "next_action": "build_hardened_container_command",
        "command": command,
        "security": {
            "read_only_root": true,
            "network": "none",
            "cap_drop": "ALL",
            "no_new_privileges": true,
            "tmpfs": ["/tmp", "/home/sandbox"],
        }
    })
}

fn canonical_sandbox_root(path: &Path) -> Result<PathBuf, String> {
    if path.exists() {
        return fs::canonicalize(path).map_err(|_| "sandbox_root_invalid".to_string());
    }
    let parent = path
        .parent()
        .ok_or_else(|| "sandbox_root_parent_missing".to_string())?;
    let file_name = path
        .file_name()
        .ok_or_else(|| "sandbox_root_file_name_missing".to_string())?;
    let canonical_parent =
        fs::canonicalize(parent).map_err(|_| "sandbox_root_parent_invalid".to_string())?;
    Ok(canonical_parent.join(file_name))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn sandbox_command_contains_required_isolation_flags() {
        let temp_root = unique_temp_dir();
        fs::create_dir_all(&temp_root).unwrap();
        let response = build_sandbox_command(&json!({"sandbox_root": temp_root}));
        assert_eq!(response["next_action"], "build_hardened_container_command");
        let command = response["command"].as_array().unwrap();
        assert!(command
            .iter()
            .any(|item| item.as_str() == Some("--read-only")));
        assert!(command
            .iter()
            .any(|item| item.as_str() == Some("--network")));
        assert!(command.iter().any(|item| item.as_str() == Some("none")));
        assert!(command
            .iter()
            .any(|item| item.as_str() == Some("--cap-drop")));
        assert!(command.iter().any(|item| item.as_str() == Some("ALL")));
        assert!(command
            .iter()
            .any(|item| item.as_str() == Some("no-new-privileges:true")));
    }

    #[test]
    fn missing_sandbox_root_blocks() {
        let response = build_sandbox_command(&json!({}));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "sandbox_root_missing");
    }

    fn unique_temp_dir() -> PathBuf {
        let millis = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_millis();
        std::env::temp_dir().join(format!("empyralis-runtime-kernel-test-{millis}"))
    }
}

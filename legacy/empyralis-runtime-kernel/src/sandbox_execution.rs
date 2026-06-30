use serde_json::{json, Map, Value};

const ALLOWED_OPERATIONS: &[&str] = &[
    "prepare_sandbox",
    "runtime_scope",
    "sandbox_profile",
    "environment",
    "base_image_build",
    "base_image_write",
    "build_docker_command",
    "docker_args",
    "launch_worker",
    "hosted_worker",
    "mount_policy",
    "network_policy",
    "env_policy",
    "artifact_path",
    "timeout_policy",
    "cleanup_policy",
    "result_normalization",
];

const ENV_ALLOWLIST: &[&str] = &[
    "DATABASE_URL",
    "EMPYRALIS_SANDBOX_OUTPUT_FILE",
    "EMPYRALIS_SANDBOX_ROOT",
    "EMPYRALIS_SANDBOX_CPU_SECONDS",
    "EMPYRALIS_SANDBOX_MEMORY_MB",
    "EMPYRALIS_SANDBOX_MAX_OPEN_FILES",
    "EMPYRALIS_SANDBOX_MAX_FILE_BYTES",
    "EMPYRALIS_SANDBOX_NETWORK_MODE",
    "EMPYRALIS_SANDBOX_ALLOW_OUTBOUND",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "PGAPPNAME",
    "PGDATABASE",
    "PGHOST",
    "PGPORT",
    "PGSERVICE",
    "PGSSLMODE",
    "PGSSLROOTCERT",
    "PGUSER",
    "PYTHONPATH",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONNOUSERSITE",
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_FILE",
    "TMPDIR",
];

const SECRET_ENV_PREFIXES: &[&str] = &[
    "ANTHROPIC_",
    "AWS_",
    "AZURE_",
    "DEEPSEEK_",
    "DOCKER_",
    "GCP_",
    "GOOGLE_",
    "GROQ_",
    "KUBECONFIG",
    "OPENAI_",
    "SSH_",
    "XAI_",
];

pub fn sandbox_execution_decision_command(payload: &Value) -> Value {
    let operation = string_field(payload, &["operation", "action"]);
    let operation = if operation.is_empty() {
        "prepare_sandbox".to_string()
    } else {
        operation
    };
    let runtime_mode = non_empty_or(
        string_field(payload, &["runtime_mode", "mode"]),
        "hosted_secure",
    );
    let driver = non_empty_or(
        string_field(payload, &["driver", "sandbox_driver"]),
        "docker",
    );
    let sandbox_root = string_field(payload, &["sandbox_root", "root", "workspace_root"]);
    let image = non_empty_or(
        string_field(payload, &["image", "base_image_id", "container_image"]),
        "empyralis-sandbox:latest",
    );
    let network_policy = object_field(payload, "network_policy");
    let mut network_mode = string_field(payload, &["network_mode"]);
    if network_mode.is_empty() {
        network_mode = string_from_object(network_policy, "mode");
    }
    if network_mode.is_empty() {
        network_mode = if bool_field(payload, &["network_enabled"], false) {
            "allow".to_string()
        } else {
            "none".to_string()
        };
    }
    let allow_outbound = bool_from_object(network_policy, "allow_outbound", false)
        || bool_field(payload, &["allow_outbound", "network_enabled"], false);
    let allow_private_hosts = bool_from_object(network_policy, "allow_private_hosts", false)
        || bool_field(payload, &["allow_private_hosts"], false);
    let hooks_ready = bool_from_object(network_policy, "hooks_ready", true);
    let read_only = bool_field(payload, &["read_only", "read_only_base_image"], true);
    let host_mounts_allowed = bool_field(payload, &["host_mounts_allowed"], false);
    let docker_socket_exposed = bool_field(payload, &["docker_socket_exposed"], false);
    let privileged = bool_field(payload, &["privileged"], false);
    let cap_drop_all = bool_field(payload, &["cap_drop_all"], true);
    let no_new_privileges = bool_field(payload, &["no_new_privileges"], true);
    let approval_provided = bool_field(
        payload,
        &["approval_provided", "network_approval", "operator_approval"],
        false,
    );
    let image_approved = bool_field(payload, &["image_approved", "base_image_approved"], false);
    let timeout_seconds = int_field(payload, &["timeout_seconds"], 25);
    let max_timeout_seconds = int_field(payload, &["max_timeout_seconds"], 300);
    let cpu_seconds = int_field(payload, &["cpu_seconds"], 20);
    let memory_mb = int_field(payload, &["memory_mb"], 512);
    let max_open_files = int_field(payload, &["max_open_files"], 64);
    let max_file_bytes = int_field(payload, &["max_file_bytes"], 16 * 1024 * 1024);
    let artifact_path = string_field(payload, &["artifact_path", "output_path", "output_file"]);
    let docker_args = strings_from_keys(payload, &["docker_args", "command", "cmd"]);
    let mount_tokens = strings_from_keys(payload, &["mounts", "bind_mounts", "volumes"]);
    let env_names = env_names(payload);

    let mut block_reasons: Vec<String> = Vec::new();
    let mut approval_reasons: Vec<String> = Vec::new();
    let mut scrubbed_env_names: Vec<String> = Vec::new();
    let mut blocked_env_names: Vec<String> = Vec::new();

    if !ALLOWED_OPERATIONS.contains(&operation.as_str()) {
        block_reasons.push(format!("unknown_sandbox_operation:{operation}"));
    }
    if root_required(&operation) {
        if sandbox_root.is_empty() {
            block_reasons.push("missing_sandbox_root".to_string());
        } else if unsafe_path_token(&sandbox_root) || broad_host_root(&sandbox_root) {
            block_reasons.push("unsafe_sandbox_root".to_string());
        }
    }
    if unsafe_image_token(&image) {
        block_reasons.push("unsafe_container_image".to_string());
    } else if !trusted_image(&image) && !image_approved {
        approval_reasons.push("custom_container_image_requires_approval".to_string());
    }
    if privileged || docker_args_contain(&docker_args, "--privileged") {
        block_reasons.push("privileged_container_blocked".to_string());
    }
    if docker_socket_exposed
        || tokens_contain(&mount_tokens, "docker.sock")
        || tokens_contain(&docker_args, "docker.sock")
    {
        block_reasons.push("docker_socket_mount_blocked".to_string());
    }
    if runtime_mode == "hosted_secure" && host_mounts_allowed {
        block_reasons.push("host_mounts_blocked_for_hosted_secure".to_string());
    } else if host_mounts_allowed && !approval_provided {
        approval_reasons.push("host_mounts_require_operator_approval".to_string());
    }
    for mount in &mount_tokens {
        if unsafe_mount(mount, &sandbox_root) {
            block_reasons.push("unsafe_host_mount".to_string());
            break;
        }
    }
    if !read_only {
        approval_reasons.push("writable_base_image_requires_approval".to_string());
    }
    if !cap_drop_all || docker_args_contain_missing(&docker_args, "--cap-drop=ALL") {
        block_reasons.push("docker_cap_drop_all_required".to_string());
    }
    if !no_new_privileges
        || docker_args_contain_missing(&docker_args, "--security-opt=no-new-privileges:true")
    {
        block_reasons.push("docker_no_new_privileges_required".to_string());
    }
    if docker_args_contain_missing(&docker_args, "--read-only") && read_only {
        block_reasons.push("docker_read_only_flag_required".to_string());
    }
    if docker_network_host(&docker_args) || network_mode == "host" {
        block_reasons.push("host_network_blocked".to_string());
    }
    if allow_private_hosts {
        block_reasons.push("private_network_hosts_blocked".to_string());
    }
    let allowlisted_network =
        network_mode == "allowlist_hooks" && hooks_ready && !allow_private_hosts;
    if (allow_outbound || network_mode != "none") && !hooks_ready {
        block_reasons.push("network_hooks_not_ready".to_string());
    }
    if (allow_outbound || network_mode != "none") && !allowlisted_network && !approval_provided {
        approval_reasons.push("network_access_requires_approval".to_string());
    }
    if !docker_args.is_empty() && network_mode == "none" && !docker_network_none(&docker_args) {
        block_reasons.push("docker_network_none_required".to_string());
    }
    for name in &env_names {
        if secret_env_name(name) {
            blocked_env_names.push(name.to_string());
        } else if !ENV_ALLOWLIST.contains(&name.as_str()) {
            scrubbed_env_names.push(name.to_string());
        }
    }
    if !blocked_env_names.is_empty() {
        block_reasons.push("secret_like_environment_blocked".to_string());
    }
    if timeout_seconds <= 0 {
        block_reasons.push("timeout_must_be_positive".to_string());
    } else if timeout_seconds > 3_600 {
        block_reasons.push("timeout_exceeds_hard_limit".to_string());
    } else if timeout_seconds > max_timeout_seconds {
        approval_reasons.push("timeout_exceeds_default_limit".to_string());
    }
    if cpu_seconds > 900 {
        block_reasons.push("cpu_limit_exceeds_hard_limit".to_string());
    } else if cpu_seconds > 120 {
        approval_reasons.push("cpu_limit_exceeds_default_limit".to_string());
    }
    if memory_mb < 128 {
        approval_reasons.push("memory_limit_below_supported_minimum".to_string());
    } else if memory_mb > 8_192 {
        block_reasons.push("memory_limit_exceeds_hard_limit".to_string());
    } else if memory_mb > 2_048 {
        approval_reasons.push("memory_limit_exceeds_default_limit".to_string());
    }
    if max_open_files > 512 {
        approval_reasons.push("open_file_limit_exceeds_default_limit".to_string());
    }
    if max_file_bytes > 128 * 1024 * 1024 {
        approval_reasons.push("artifact_size_limit_exceeds_default_limit".to_string());
    }
    if !artifact_path.is_empty() {
        if unsafe_path_token(&artifact_path) {
            block_reasons.push("unsafe_artifact_path".to_string());
        } else if artifact_path.starts_with('/')
            && !artifact_path.starts_with("/workspace/")
            && !sandbox_root.is_empty()
            && !artifact_path.starts_with(&sandbox_root)
        {
            block_reasons.push("artifact_path_outside_sandbox".to_string());
        }
    }

    block_reasons.sort();
    block_reasons.dedup();
    approval_reasons.sort();
    approval_reasons.dedup();
    scrubbed_env_names.sort();
    scrubbed_env_names.dedup();
    blocked_env_names.sort();
    blocked_env_names.dedup();

    let decision = if !block_reasons.is_empty() {
        "block"
    } else if !approval_reasons.is_empty() && !approval_provided {
        "require_approval"
    } else {
        "allow"
    };
    let reason = if !block_reasons.is_empty() {
        block_reasons.join("; ")
    } else if decision == "require_approval" {
        approval_reasons.join("; ")
    } else {
        "sandbox_execution_policy_satisfied".to_string()
    };
    let clamped_timeout_seconds = timeout_seconds.max(1).min(max_timeout_seconds.max(1));

    json!({
        "ok": decision != "block",
        "decision": decision,
        "reason": reason,
        "operation": operation,
        "next_action": next_action(&operation, decision),
        "approval_required": decision == "require_approval",
        "audit_visibility": "security_audit_required",
        "runtime_mode": runtime_mode,
        "driver": driver,
        "sandbox": {
            "sandbox_root": sandbox_root,
            "image": image,
            "read_only": read_only,
            "clamped_timeout_seconds": clamped_timeout_seconds,
            "memory_mb": memory_mb.max(128),
            "cpu_seconds": cpu_seconds.max(1),
            "max_open_files": max_open_files.max(32),
            "max_file_bytes": max_file_bytes.max(1024 * 1024),
        },
        "network_policy": {
            "mode": network_mode,
            "allow_outbound": allow_outbound,
            "allow_private_hosts": allow_private_hosts,
            "hooks_ready": hooks_ready,
            "requires_approval": (allow_outbound || network_mode != "none") && !approval_provided,
        },
        "mount_policy": {
            "host_mounts_allowed": host_mounts_allowed,
            "docker_socket_exposed": docker_socket_exposed,
            "sandbox_mount_only": !host_mounts_allowed,
            "required_flags": ["--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges:true"],
        },
        "env_policy": {
            "allowlist": ENV_ALLOWLIST,
            "scrubbed_env_names": scrubbed_env_names,
            "blocked_env_names": blocked_env_names,
        },
        "artifact_policy": {
            "artifact_path": artifact_path,
            "must_remain_inside_sandbox": true,
        },
        "block_reasons": block_reasons,
        "approval_reasons": approval_reasons,
    })
}

fn string_field(payload: &Value, keys: &[&str]) -> String {
    for key in keys {
        if let Some(value) = payload.get(*key) {
            if let Some(text) = value.as_str() {
                let trimmed = text.trim();
                if !trimmed.is_empty() {
                    return trimmed.to_string();
                }
            }
        }
    }
    String::new()
}

fn non_empty_or(value: String, fallback: &str) -> String {
    if value.is_empty() {
        fallback.to_string()
    } else {
        value
    }
}

fn bool_field(payload: &Value, keys: &[&str], default: bool) -> bool {
    for key in keys {
        if let Some(value) = payload.get(*key) {
            if let Some(flag) = value.as_bool() {
                return flag;
            }
            if let Some(text) = value.as_str() {
                return matches!(
                    text.trim().to_ascii_lowercase().as_str(),
                    "1" | "true" | "yes" | "on"
                );
            }
        }
    }
    default
}

fn int_field(payload: &Value, keys: &[&str], default: i64) -> i64 {
    for key in keys {
        if let Some(value) = payload.get(*key) {
            if let Some(number) = value.as_i64() {
                return number;
            }
            if let Some(text) = value.as_str() {
                if let Ok(number) = text.trim().parse::<i64>() {
                    return number;
                }
            }
        }
    }
    default
}

fn object_field<'a>(payload: &'a Value, key: &str) -> Option<&'a Map<String, Value>> {
    payload.get(key).and_then(Value::as_object)
}

fn string_from_object(object: Option<&Map<String, Value>>, key: &str) -> String {
    object
        .and_then(|map| map.get(key))
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string()
}

fn bool_from_object(object: Option<&Map<String, Value>>, key: &str, default: bool) -> bool {
    if let Some(value) = object.and_then(|map| map.get(key)) {
        if let Some(flag) = value.as_bool() {
            return flag;
        }
        if let Some(text) = value.as_str() {
            return matches!(
                text.trim().to_ascii_lowercase().as_str(),
                "1" | "true" | "yes" | "on"
            );
        }
    }
    default
}

fn strings_from_keys(payload: &Value, keys: &[&str]) -> Vec<String> {
    let mut values = Vec::new();
    for key in keys {
        if let Some(value) = payload.get(*key) {
            collect_strings(value, &mut values);
        }
    }
    values
}

fn collect_strings(value: &Value, values: &mut Vec<String>) {
    match value {
        Value::String(text) => {
            let trimmed = text.trim();
            if !trimmed.is_empty() {
                values.push(trimmed.to_string());
            }
        }
        Value::Array(items) => {
            for item in items {
                collect_strings(item, values);
            }
        }
        Value::Object(map) => {
            for (key, value) in map {
                let trimmed = key.trim();
                if !trimmed.is_empty() {
                    values.push(trimmed.to_string());
                }
                collect_strings(value, values);
            }
        }
        _ => {}
    }
}

fn env_names(payload: &Value) -> Vec<String> {
    let mut names = strings_from_keys(payload, &["env_names", "env_keys"]);
    if let Some(environment) = payload.get("environment").or_else(|| payload.get("env")) {
        if let Some(map) = environment.as_object() {
            for key in map.keys() {
                let trimmed = key.trim();
                if !trimmed.is_empty() {
                    names.push(trimmed.to_string());
                }
            }
        } else {
            collect_strings(environment, &mut names);
        }
    }
    names.sort();
    names.dedup();
    names
}

fn root_required(operation: &str) -> bool {
    matches!(
        operation,
        "prepare_sandbox"
            | "sandbox_profile"
            | "environment"
            | "base_image_build"
            | "base_image_write"
            | "build_docker_command"
            | "docker_args"
            | "launch_worker"
            | "hosted_worker"
            | "artifact_path"
            | "cleanup_policy"
    )
}

fn unsafe_path_token(value: &str) -> bool {
    value.contains('\0')
        || value.contains("..")
        || value.contains('\n')
        || value.contains('\r')
        || value.contains('`')
        || value.contains("$(")
}

fn broad_host_root(value: &str) -> bool {
    matches!(
        value.trim(),
        "/" | "/tmp" | "/var" | "/private" | "/Users" | "/home" | "/etc" | "/System"
    )
}

fn unsafe_image_token(value: &str) -> bool {
    value.trim().is_empty()
        || value.contains('\0')
        || value.contains('\n')
        || value.contains('\r')
        || value.contains(';')
        || value.contains('|')
        || value.contains('&')
        || value.contains('`')
        || value.contains("$(")
        || value.contains("://")
        || value.contains("..")
}

fn trusted_image(value: &str) -> bool {
    value == "empyralis-hosted-secure-v1"
        || value.starts_with("empyralis-")
        || value.starts_with("ghcr.io/empyralis/")
}

fn tokens_contain(tokens: &[String], needle: &str) -> bool {
    tokens.iter().any(|token| token.contains(needle))
}

fn docker_args_contain(tokens: &[String], needle: &str) -> bool {
    tokens_contain(tokens, needle)
}

fn docker_args_contain_missing(tokens: &[String], needle: &str) -> bool {
    !tokens.is_empty() && !tokens_contain(tokens, needle)
}

fn docker_network_host(tokens: &[String]) -> bool {
    let joined = tokens.join(" ");
    joined.contains("--network=host") || joined.contains("--network host")
}

fn docker_network_none(tokens: &[String]) -> bool {
    let joined = tokens.join(" ");
    joined.contains("--network=none") || joined.contains("--network none")
}

fn unsafe_mount(mount: &str, sandbox_root: &str) -> bool {
    if mount.contains('\0') || mount.contains("..") || mount.contains("docker.sock") {
        return true;
    }
    let sensitive = [
        "/etc",
        "/System",
        "/Users",
        "/home",
        "/private/etc",
        "/var/run",
    ];
    sensitive.iter().any(|prefix| {
        mount.starts_with(prefix) && (sandbox_root.is_empty() || !mount.starts_with(sandbox_root))
    })
}

fn secret_env_name(name: &str) -> bool {
    let upper = name.trim().to_ascii_uppercase();
    if ENV_ALLOWLIST.contains(&upper.as_str()) {
        return false;
    }
    SECRET_ENV_PREFIXES
        .iter()
        .any(|prefix| upper.starts_with(prefix))
        || upper.contains("TOKEN")
        || upper.contains("SECRET")
        || upper.contains("PASSWORD")
        || upper.contains("CREDENTIAL")
        || upper.contains("PRIVATE_KEY")
        || upper.contains("COOKIE")
}

fn next_action(operation: &str, decision: &str) -> &'static str {
    if decision == "block" {
        return "stop_sandbox_execution";
    }
    if decision == "require_approval" {
        return "request_sandbox_execution_approval";
    }
    match operation {
        "prepare_sandbox" => "prepare_isolated_workspace",
        "runtime_scope" => "select_runtime_scope",
        "sandbox_profile" => "build_restricted_sandbox_profile",
        "environment" => "scrub_environment",
        "base_image_build" => "build_approved_base_image",
        "base_image_write" => "write_read_only_base_image",
        "build_docker_command" | "docker_args" => "build_hardened_container_command",
        "launch_worker" | "hosted_worker" => "launch_hosted_worker",
        "mount_policy" => "apply_mount_policy",
        "network_policy" => "apply_network_policy",
        "env_policy" => "scrub_environment",
        "artifact_path" => "write_artifact_inside_sandbox",
        "timeout_policy" => "apply_timeout_limits",
        "cleanup_policy" => "cleanup_ephemeral_workspace",
        "result_normalization" => "normalize_sandbox_result",
        _ => "review_sandbox_execution",
    }
}

#[cfg(test)]
mod sandbox_execution_extension_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn environment_policy_allows_sandbox_variables_and_scrubs_unknowns() {
        let decision = sandbox_execution_decision_command(&json!({
            "operation": "environment",
            "runtime_mode": "hosted_secure",
            "driver": "sandbox_exec",
            "sandbox_root": "/tmp/empyralis-hosted-secure-proof",
            "image": "empyralis-hosted-secure-v1",
            "image_approved": true,
            "network_policy": {"mode": "allowlist_hooks", "allow_outbound": true, "hooks_ready": true},
            "env_names": [
                "EMPYRALIS_SANDBOX_ROOT",
                "EMPYRALIS_SANDBOX_OUTPUT_FILE",
                "EMPYRALIS_SANDBOX_CPU_SECONDS",
                "PYTHONPATH",
                "UNRELATED_DEBUG_FLAG"
            ]
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["next_action"], "scrub_environment");
        assert_eq!(
            decision["env_policy"]["scrubbed_env_names"],
            json!(["UNRELATED_DEBUG_FLAG"])
        );
        assert_eq!(decision["env_policy"]["blocked_env_names"], json!([]));
    }

    #[test]
    fn environment_policy_blocks_secret_like_runtime_variables() {
        let decision = sandbox_execution_decision_command(&json!({
            "operation": "environment",
            "runtime_mode": "hosted_secure",
            "driver": "sandbox_exec",
            "sandbox_root": "/tmp/empyralis-hosted-secure-proof",
            "image": "empyralis-hosted-secure-v1",
            "image_approved": true,
            "network_policy": {"mode": "allowlist_hooks", "allow_outbound": true, "hooks_ready": true},
            "env_names": ["OPENAI_API_KEY"]
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "secret_like_environment_blocked");
        assert_eq!(
            decision["env_policy"]["blocked_env_names"],
            json!(["OPENAI_API_KEY"])
        );
    }

    #[test]
    fn sandbox_profile_policy_allows_hosted_secure_allowlisted_network() {
        let decision = sandbox_execution_decision_command(&json!({
            "operation": "sandbox_profile",
            "runtime_mode": "hosted_secure",
            "driver": "sandbox_exec",
            "sandbox_root": "/tmp/empyralis-hosted-secure-proof",
            "image": "empyralis-hosted-secure-v1",
            "image_approved": true,
            "read_only": true,
            "host_mounts_allowed": false,
            "docker_socket_exposed": false,
            "network_policy": {
                "mode": "allowlist_hooks",
                "allow_outbound": true,
                "allow_private_hosts": false,
                "hooks_ready": true
            }
        }));

        assert_eq!(decision["decision"], "allow");
        assert_eq!(decision["next_action"], "build_restricted_sandbox_profile");
        assert_eq!(decision["mount_policy"]["sandbox_mount_only"], true);
    }

    #[test]
    fn base_image_build_blocks_unsafe_image_tokens() {
        let decision = sandbox_execution_decision_command(&json!({
            "operation": "base_image_build",
            "runtime_mode": "hosted_secure",
            "driver": "docker",
            "sandbox_root": "/tmp/empyralis-hosted-secure-proof",
            "image": "empyralis-sandbox:latest;rm -rf /",
            "image_approved": true,
            "read_only": true,
            "host_mounts_allowed": false,
            "docker_socket_exposed": false,
            "network_policy": {"mode": "none", "allow_outbound": false, "hooks_ready": true}
        }));

        assert_eq!(decision["decision"], "block");
        assert_eq!(decision["reason"], "unsafe_container_image");
    }
}

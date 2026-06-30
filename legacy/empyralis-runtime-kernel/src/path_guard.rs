use crate::protocol;
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};

pub fn check_path_containment_command(input: &Value) -> Value {
    let Some(raw_path) = protocol::string_field(input, "path") else {
        return protocol::block("path_missing");
    };
    let allowed_roots = protocol::string_vec_field(input, "allowed_roots");
    if allowed_roots.is_empty() {
        return protocol::block("allowed_roots_missing");
    }

    let candidate = match canonical_candidate(&PathBuf::from(raw_path)) {
        Ok(path) => path,
        Err(reason) => return protocol::block(&reason),
    };

    for blocked_root in protocol::string_vec_field(input, "blocked_roots") {
        match fs::canonicalize(PathBuf::from(blocked_root)) {
            Ok(root) if candidate.starts_with(&root) => {
                return protocol::block("path_blocked_by_root")
            }
            Err(_) => return protocol::block("blocked_root_invalid"),
            _ => {}
        }
    }

    for allowed_root in allowed_roots {
        match fs::canonicalize(PathBuf::from(allowed_root)) {
            Ok(root) if candidate.starts_with(&root) => {
                return json!({
                    "ok": true,
                    "decision": "allow",
                    "reason": "path_within_allowed_root",
                    "next_action": "allow_path_access",
                    "canonical_path": candidate.display().to_string(),
                    "allowed_root": root.display().to_string(),
                });
            }
            Err(_) => return protocol::block("allowed_root_invalid"),
            _ => {}
        }
    }

    protocol::block("path_outside_allowed_roots")
}

fn canonical_candidate(path: &Path) -> Result<PathBuf, String> {
    if path.exists() {
        return fs::canonicalize(path).map_err(|_| "path_invalid".to_string());
    }
    let parent = path
        .parent()
        .ok_or_else(|| "path_parent_missing".to_string())?;
    let file_name = path
        .file_name()
        .ok_or_else(|| "path_file_name_missing".to_string())?;
    let canonical_parent =
        fs::canonicalize(parent).map_err(|_| "path_parent_invalid".to_string())?;
    Ok(canonical_parent.join(file_name))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn missing_allowed_roots_blocks() {
        let response = check_path_containment_command(&json!({"path": "/tmp/example"}));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "allowed_roots_missing");
    }

    #[test]
    fn contained_path_allows() {
        let root = unique_temp_dir("allowed");
        fs::create_dir_all(&root).unwrap();
        let file_path = root.join("file.txt");
        fs::write(&file_path, "ok").unwrap();
        let response = check_path_containment_command(&json!({
            "path": file_path,
            "allowed_roots": [root],
        }));
        assert_eq!(response["decision"], "allow");
        assert_eq!(response["next_action"], "allow_path_access");
    }

    #[test]
    fn blocked_root_wins() {
        let root = unique_temp_dir("allowed-blocked");
        let blocked = root.join("secret");
        fs::create_dir_all(&blocked).unwrap();
        let file_path = blocked.join("file.txt");
        fs::write(&file_path, "no").unwrap();
        let response = check_path_containment_command(&json!({
            "path": file_path,
            "allowed_roots": [root],
            "blocked_roots": [blocked],
        }));
        assert_eq!(response["decision"], "block");
        assert_eq!(response["reason"], "path_blocked_by_root");
    }

    fn unique_temp_dir(label: &str) -> PathBuf {
        let millis = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_millis();
        std::env::temp_dir().join(format!("empyralis-runtime-kernel-{label}-{millis}"))
    }
}

use anyhow::{bail, Context, Result};
use serde::Deserialize;
use serde_json::{json, Value};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Deserialize)]
struct FilesystemArguments {
    path: String,
    mode: Option<String>,
    content: Option<String>,
    overwrite: Option<bool>,
}

// ── Hard-protected path markers (NEVER bypassed, even in full_access) ──
// These directories contain credentials, pairings, runtime state, and
// worker config.  Deleting or overwriting files here forces re-login,
// re-pair, or re-setup.  The agent can destroy its scope; it can NEVER
// destroy the account.
const HARD_PROTECTED_PATH_MARKERS: &[&str] = &[
    "/.empyralis/state/vault",
    "/.empyralis/state",
    "/.ssh",
    "/.gnupg",
    "/etc/empyralis",
    "/var/lib/empyralis/agent-computer",
    "/.orion-stack",
];

pub fn read_write(arguments: &Value, full_access: bool, allowed_roots: &[String]) -> Result<Value> {
    let args: FilesystemArguments = serde_json::from_value(arguments.clone())
        .context("invalid filesystem.read_write arguments")?;
    let mode = args.mode.as_deref().unwrap_or("read").trim().to_lowercase();
    let target = resolve_path(&args.path)?;
    let display_path = target.to_string_lossy().to_string();

    // ── Hard-protected roots — ALWAYS enforced, even in full_access ──
    // Write/delete/append to protected paths is refused outright.
    // Read is still allowed (the agent can see these paths exist but cannot
    // mutate them).
    if mode != "read" && hard_protected_root(&target) {
        bail!(
            "filesystem.{} path is permanently protected — it contains credentials, pairings, or runtime state that must survive agent actions",
            mode
        );
    }

    if !full_access {
        enforce_path_policy(&target, allowed_roots)?;
    }
    if !full_access && sensitive_path(&target) {
        bail!("filesystem.read_write path is blocked by the Agent Computer policy");
    }

    match mode.as_str() {
        "read" => {
            if !target.exists() {
                bail!("path not found: {display_path}");
            }
            if target.is_dir() {
                let mut entries: Vec<String> = fs::read_dir(&target)
                    .with_context(|| format!("failed to read directory: {display_path}"))?
                    .filter_map(|entry| entry.ok())
                    .map(|entry| {
                        let file_name = entry.file_name().to_string_lossy().to_string();
                        match entry.file_type() {
                            Ok(kind) if kind.is_dir() => format!("{file_name}/"),
                            _ => file_name,
                        }
                    })
                    .collect();
                entries.sort();
                return Ok(json!({
                    "mode": "read",
                    "path": display_path,
                    "is_directory": true,
                    "entries": entries,
                }));
            }
            let content = fs::read_to_string(&target)
                .with_context(|| format!("failed to read file: {display_path}"))?;
            Ok(json!({
                "mode": "read",
                "path": display_path,
                "is_directory": false,
                "content": content,
            }))
        }
        "write" => {
            if target.exists() && !args.overwrite.unwrap_or(false) {
                bail!("file already exists: {display_path}");
            }
            let content = args.content.unwrap_or_default();
            if let Some(parent) = target.parent() {
                fs::create_dir_all(parent).with_context(|| {
                    format!("failed to create parent directory for: {display_path}")
                })?;
            }
            fs::write(&target, content.as_bytes())
                .with_context(|| format!("failed to write file: {display_path}"))?;
            Ok(json!({
                "mode": "write",
                "path": display_path,
                "bytes_written": content.as_bytes().len(),
            }))
        }
        "append" => {
            let content = args.content.unwrap_or_default();
            if let Some(parent) = target.parent() {
                fs::create_dir_all(parent).with_context(|| {
                    format!("failed to create parent directory for: {display_path}")
                })?;
            }
            let mut existing = if target.exists() {
                fs::read(&target)
                    .with_context(|| format!("failed to read file for append: {display_path}"))?
            } else {
                Vec::new()
            };
            existing.extend_from_slice(content.as_bytes());
            fs::write(&target, existing)
                .with_context(|| format!("failed to append file: {display_path}"))?;
            Ok(json!({
                "mode": "append",
                "path": display_path,
                "bytes_written": content.as_bytes().len(),
            }))
        }
        "delete" => {
            if !target.exists() {
                bail!("path not found: {display_path}");
            }
            if target.is_dir() {
                bail!("directory deletion is not supported");
            }
            fs::remove_file(&target)
                .with_context(|| format!("failed to delete file: {display_path}"))?;
            Ok(json!({
                "mode": "delete",
                "path": display_path,
            }))
        }
        _ => bail!("filesystem.read_write mode must be read, write, append, or delete."),
    }
}

fn resolve_path(raw_path: &str) -> Result<PathBuf> {
    let trimmed = raw_path.trim();
    if trimmed.is_empty() {
        bail!("path is required");
    }
    let expanded = expand_home_alias(trimmed);
    let path = PathBuf::from(expanded);
    if path.is_absolute() {
        return Ok(path);
    }
    let cwd = env::current_dir().context("failed to resolve current directory")?;
    Ok(cwd.join(path))
}

fn enforce_path_policy(target: &Path, allowed_roots: &[String]) -> Result<()> {
    let target = normalize_for_policy(target)?;
    let roots = allowed_roots
        .iter()
        .filter_map(|raw| {
            let token = raw.trim();
            if token.is_empty() || token == "*" {
                None
            } else {
                Some(resolve_path(token))
            }
        })
        .collect::<Result<Vec<_>>>()?;
    let effective_roots = if roots.is_empty() {
        vec![env::current_dir().context("failed to resolve current directory")?]
    } else {
        roots
    };
    for root in effective_roots {
        let root = normalize_for_policy(&root)?;
        if target == root || target.starts_with(&root) {
            return Ok(());
        }
    }
    bail!("filesystem.read_write path is outside the Agent Computer policy");
}

fn normalize_for_policy(path: &Path) -> Result<PathBuf> {
    if path.exists() {
        return fs::canonicalize(path)
            .with_context(|| format!("failed to canonicalize path: {}", path.to_string_lossy()));
    }
    if let Some(parent) = path.parent() {
        if parent.exists() {
            let parent = fs::canonicalize(parent).with_context(|| {
                format!(
                    "failed to canonicalize parent path: {}",
                    parent.to_string_lossy()
                )
            })?;
            if let Some(file_name) = path.file_name() {
                return Ok(parent.join(file_name));
            }
        }
    }
    Ok(path.to_path_buf())
}

fn sensitive_path(path: &Path) -> bool {
    let normalized = path.to_string_lossy().to_lowercase();
    let name = path
        .file_name()
        .map(|value| value.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    if name == ".env"
        || name.starts_with(".env.")
        || name == "id_rsa"
        || name == "id_ed25519"
        || name == "known_hosts"
        || name.ends_with(".pem")
        || name.ends_with(".key")
        || name.ends_with(".p12")
    {
        return true;
    }
    normalized.contains("/.ssh/")
        || normalized.contains("/.gws-config/")
        || normalized.contains("/.orion-stack/")
        || normalized.contains("/.local-dev-state/")
        || normalized.contains("token_cache")
        || normalized.contains("credentials")
}

// ── Protected roots: ALWAYS enforced, NEVER bypassed by full_access ──
// These paths contain state whose loss forces re-login, re-pair, or
// re-setup.  The agent can destroy its workspace scope; it can NEVER
// destroy credentials, pairings, SSH keys, or worker config.
fn hard_protected_root(path: &Path) -> bool {
    // Resolve symlinks before checking — prevents bypass via
    // ln -s ~/.empyralis/state/vault ~/workspace/link && rm -rf ~/workspace/link
    let check_path = if path.exists() {
        fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf())
    } else if let Some(parent) = path.parent() {
        if parent.exists() {
            // For not-yet-existing files, resolve the parent and join filename
            match fs::canonicalize(parent) {
                Ok(canonical_parent) => canonical_parent.join(
                    path.file_name().unwrap_or_default()
                ),
                Err(_) => path.to_path_buf(),
            }
        } else {
            path.to_path_buf()
        }
    } else {
        path.to_path_buf()
    };

    let normalized = check_path.to_string_lossy().to_lowercase();
    // Exact protected filenames (regardless of directory)
    let name = check_path
        .file_name()
        .map(|value| value.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    // The vault encryption key file (named "key" without extension)
    if name == "key"
        && (normalized.contains("/.empyralis/state/vault")
            || normalized.contains("/.orion-stack"))
    {
        return true;
    }
    for marker in HARD_PROTECTED_PATH_MARKERS {
        if normalized.contains(marker) {
            return true;
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::read_write;
    use serde_json::json;
    use std::fs;

    fn temp_dir(name: &str) -> std::path::PathBuf {
        let root = std::env::temp_dir().join(format!(
            "empyralis-supervisor-filesystem-{name}-{}",
            uuid::Uuid::new_v4()
        ));
        fs::create_dir_all(&root).expect("create temp dir");
        root
    }

    #[test]
    fn default_mode_blocks_sensitive_files() {
        let root = temp_dir("default");
        let env_path = root.join(".env");
        fs::write(&env_path, "SECRET=value").expect("write env file");
        let result = read_write(
            &json!({"mode": "read", "path": env_path.to_string_lossy()}),
            false,
            &[root.to_string_lossy().to_string()],
        );
        assert!(result.is_err());
    }

    #[test]
    fn custom_mode_allows_only_granted_paths() {
        let allowed = temp_dir("allowed");
        let denied = temp_dir("denied");
        let allowed_file = allowed.join("note.txt");
        let denied_file = denied.join("note.txt");
        fs::write(&allowed_file, "allowed").expect("write allowed file");
        fs::write(&denied_file, "denied").expect("write denied file");

        assert!(read_write(
            &json!({"mode": "read", "path": allowed_file.to_string_lossy()}),
            false,
            &[allowed.to_string_lossy().to_string()],
        )
        .is_ok());
        assert!(read_write(
            &json!({"mode": "read", "path": denied_file.to_string_lossy()}),
            false,
            &[allowed.to_string_lossy().to_string()],
        )
        .is_err());
    }

    #[test]
    fn full_access_can_read_and_delete_sensitive_files() {
        let root = temp_dir("full");
        let env_path = root.join(".env");
        fs::write(&env_path, "SECRET=value").expect("write env file");

        assert!(read_write(
            &json!({"mode": "read", "path": env_path.to_string_lossy()}),
            true,
            &[],
        )
        .is_ok());
        assert!(read_write(
            &json!({"mode": "delete", "path": env_path.to_string_lossy()}),
            true,
            &[],
        )
        .is_ok());
        assert!(!env_path.exists());
    }

    // ── Hard-protected root tests (NEVER bypassed, even in full_access) ──

    fn temp_protected_dir(name: &str) -> std::path::PathBuf {
        let root = std::env::temp_dir().join(format!(
            "empyralis-supervisor-protected-{name}-{}",
            uuid::Uuid::new_v4()
        ));
        fs::create_dir_all(&root).expect("create temp dir");
        root
    }

    #[test]
    fn full_access_cannot_delete_files_in_vault_path() {
        // Simulate a path containing ~/.empyralis/state/vault
        let root = temp_protected_dir("vault");
        let vault_subdir = root.join(".empyralis").join("state").join("vault");
        fs::create_dir_all(&vault_subdir).expect("create vault dir");
        let cred_file = vault_subdir.join("credentials.json");
        fs::write(&cred_file, "secret").expect("write cred file");

        // Read should be allowed even in protected dirs
        assert!(read_write(
            &json!({"mode": "read", "path": cred_file.to_string_lossy()}),
            true,  // full_access
            &[],
        )
        .is_ok());

        // Write should be BLOCKED in protected dirs, even with full_access
        let new_file = vault_subdir.join("new_credential.json");
        assert!(read_write(
            &json!({"mode": "write", "path": new_file.to_string_lossy(), "content": "malicious"}),
            true,  // full_access
            &[],
        )
        .is_err());

        // Delete should be BLOCKED in protected dirs, even with full_access
        assert!(read_write(
            &json!({"mode": "delete", "path": cred_file.to_string_lossy()}),
            true,  // full_access
            &[],
        )
        .is_err());
        // File should still exist
        assert!(cred_file.exists());
    }

    #[test]
    fn full_access_cannot_delete_files_in_ssh_path() {
        let root = temp_protected_dir("ssh");
        let ssh_dir = root.join(".ssh");
        fs::create_dir_all(&ssh_dir).expect("create .ssh dir");
        let key_file = ssh_dir.join("id_rsa");
        fs::write(&key_file, "private-key").expect("write key file");

        // Read allowed
        assert!(read_write(
            &json!({"mode": "read", "path": key_file.to_string_lossy()}),
            true,
            &[],
        )
        .is_ok());

        // Delete blocked
        assert!(read_write(
            &json!({"mode": "delete", "path": key_file.to_string_lossy()}),
            true,
            &[],
        )
        .is_err());
        assert!(key_file.exists());
    }

    #[test]
    fn full_access_still_allows_rw_in_non_protected_scratch_paths() {
        // The whole point: agent CAN destroy its workspace scope
        let scratch = temp_dir("scratch");
        let workspace = scratch.join("workspace");
        fs::create_dir_all(&workspace).expect("create workspace");
        let data = workspace.join("output.txt");
        fs::write(&data, "workspace data").expect("write data");

        // Write allowed (not a protected path)
        assert!(read_write(
            &json!({"mode": "write", "path": data.to_string_lossy(), "content": "modified", "overwrite": true}),
            true,
            &[],
        )
        .is_ok());

        // Delete allowed (not a protected path)
        assert!(read_write(
            &json!({"mode": "delete", "path": data.to_string_lossy()}),
            true,
            &[],
        )
        .is_ok());
        assert!(!data.exists());
    }

    #[test]
    fn symlink_bypass_to_vault_is_blocked() {
        // Agent creates ~/workspace/vault_link → ~/.empyralis/state/vault
        // then tries to delete ~/workspace/vault_link/credentials.json
        // The canonicalized path resolves to the vault — MUST be blocked.
        let root = temp_protected_dir("symlink");
        let vault_dir = root.join(".empyralis").join("state").join("vault");
        fs::create_dir_all(&vault_dir).expect("create vault dir");
        let cred_file = vault_dir.join("credentials.json");
        fs::write(&cred_file, "secret").expect("write cred file");

        let workspace = root.join("workspace");
        fs::create_dir_all(&workspace).expect("create workspace dir");
        let link_path = workspace.join("vault_link");

        // Create symlink: workspace/vault_link → .empyralis/state/vault
        #[cfg(unix)]
        std::os::unix::fs::symlink(&vault_dir, &link_path).expect("create symlink");

        // Try to delete through the symlink — must be blocked
        let target = link_path.join("credentials.json");
        // The path must exist for canonicalize to work
        assert!(target.exists());

        let result = read_write(
            &json!({"mode": "delete", "path": target.to_string_lossy()}),
            true,  // full_access
            &[],
        );
        assert!(result.is_err(), "symlink bypass to vault must be blocked");
        assert!(cred_file.exists(), "original vault file must still exist");
    }
}

fn expand_home_alias(raw_path: &str) -> String {
    let value = raw_path.trim();
    if let Some(rest) = value.strip_prefix("~/") {
        if let Ok(home) = env::var("HOME") {
            return Path::new(&home).join(rest).to_string_lossy().to_string();
        }
    }
    value.to_string()
}

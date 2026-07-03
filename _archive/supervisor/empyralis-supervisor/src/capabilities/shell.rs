use crate::execution::ExecutionContext;
use anyhow::{bail, Context, Result};
use serde::Deserialize;
use serde_json::{json, Value};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread;
use std::time::Duration;

#[derive(Debug, Deserialize)]
struct ShellArguments {
    command: String,
}

const LOCAL_SYSTEM_INFO_PROBE_COMPACT: &str = r#"printf "===os===\n"; (sw_vers 2>/dev/null || uname -a); printf "\n===cpu===\n"; (sysctl -n machdep.cpu.brand_string 2>/dev/null || uname -m); printf "\n===ram_bytes===\n"; (sysctl -n hw.memsize 2>/dev/null || true); printf "\n===cores===\n"; (sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || true); printf "\n===disk===\n"; (df -h / 2>/dev/null || true)"#;

// ── Hard-blocked command patterns (NEVER bypassed, even in full_access) ──
// These are catastrophic commands whose execution causes irreversible
// destruction.  They are refused ALWAYS — before any policy check.
// Principle: the agent can destroy its scope; it can NEVER destroy the
// account, credentials, pairings, or runtime.
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

// ── Hard-protected path markers (NEVER bypassed) ──
// Any shell command whose arguments include a path matching these markers
// is refused outright.  These directories contain credentials, pairings,
// runtime state, and worker config — losing them forces re-login,
// re-pair, or re-setup.
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
        .to_lowercase();
    if compact.is_empty() {
        return false;
    }
    // Normalize repeated spaces for pattern matching
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
        let cleaned = token.trim_matches('"').trim_matches('\'').to_lowercase();
        for marker in HARD_PROTECTED_PATH_MARKERS {
            if cleaned.contains(marker) {
                return true;
            }
        }
        // Resolve symlinks: the token might be a symlink to a protected path
        // (e.g. ln -s ~/.empyralis/state/vault ~/workspace/link; rm link)
        if looks_like_path_argument(token) {
            if let Ok(resolved) = resolve_path(token) {
                if let Ok(canonical) = fs::canonicalize(&resolved) {
                    let canonical_str = canonical.to_string_lossy().to_lowercase();
                    for marker in HARD_PROTECTED_PATH_MARKERS {
                        if canonical_str.contains(marker) {
                            return true;
                        }
                    }
                }
            }
        }
    }
    false
}

pub fn execute(
    arguments: &Value,
    context: &ExecutionContext,
    trusted_execution: bool,
    allowed_roots: &[String],
) -> Result<Value> {
    let args: ShellArguments =
        serde_json::from_value(arguments.clone()).context("invalid shell.execute arguments")?;
    let command = args.command.trim();
    if command.is_empty() {
        bail!("command is required");
    }

    // ── Hard blocks — ALWAYS enforced, even in full_access ──
    if hard_blocked_command(command) {
        bail!(
            "shell.execute command is permanently blocked — it could cause irreversible destruction of the runtime environment"
        );
    }
    if command_touches_protected_path(command) {
        bail!(
            "shell.execute path is permanently protected — it contains credentials, pairings, or runtime state"
        );
    }

    if !trusted_execution {
        if !safe_shell_command(command) {
            bail!("shell.execute command is not allowed");
        }
        enforce_shell_path_policy(command, allowed_roots)?;
    }

    context.check_cancelled()?;
    #[cfg(target_os = "windows")]
    let mut child = Command::new("powershell")
        .args(["-NoProfile", "-Command", command])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .context("failed to execute shell command")?;

    #[cfg(not(target_os = "windows"))]
    let mut child = Command::new("/bin/zsh")
        .args(["-lc", command])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .context("failed to execute shell command")?;

    loop {
        context.check_cancelled().or_else(|error| {
            let _ = child.kill();
            Err(error)
        })?;
        if child
            .try_wait()
            .context("failed to poll shell command")?
            .is_some()
        {
            break;
        }
        thread::sleep(Duration::from_millis(50));
    }

    let output = child
        .wait_with_output()
        .context("failed to collect shell command output")?;

    context.check_cancelled()?;
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    if !output.status.success() {
        bail!(
            "{}",
            if stderr.is_empty() {
                format!(
                    "command failed with exit code {}",
                    output.status.code().unwrap_or(1)
                )
            } else {
                stderr
            }
        );
    }
    Ok(json!({
        "command": command,
        "exit_code": output.status.code().unwrap_or(0),
        "stdout": stdout,
        "stderr": stderr,
    }))
}

fn safe_shell_command(command: &str) -> bool {
    let compact = command
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase();
    if compact.is_empty() {
        return false;
    }
    if compact == LOCAL_SYSTEM_INFO_PROBE_COMPACT {
        return true;
    }
    for blocked in ["&&", "||", ";", "|", ">", "<", "$(", "`"] {
        if compact.contains(blocked) {
            return false;
        }
    }
    let tokens: Vec<&str> = compact.split_whitespace().collect();
    let first = match tokens.first() {
        Some(token) => *token,
        None => return false,
    };
    let blocked_commands = [
        "rm", "mv", "cp", "chmod", "chown", "mkdir", "touch", "tee", "echo", "python", "python3",
        "node", "bash", "zsh", "sh", "kill", "xargs", "perl", "ruby", "git", "curl", "wget", "scp",
        "rsync",
    ];
    if blocked_commands.contains(&first) {
        return false;
    }
    match first {
        "ls" | "pwd" | "find" | "head" | "tail" | "cat" | "wc" | "stat" | "file" | "du"
        | "mdls" | "tree" | "rg" | "grep" | "readlink" | "dirname" | "basename" => true,
        "sed" => tokens.get(1).copied() == Some("-n"),
        _ => false,
    }
}

fn enforce_shell_path_policy(command: &str, allowed_roots: &[String]) -> Result<()> {
    let compact = command.split_whitespace().collect::<Vec<_>>().join(" ");
    if compact == LOCAL_SYSTEM_INFO_PROBE_COMPACT {
        return Ok(());
    }
    let tokens = shell_tokens(&compact);
    let first = tokens.first().map(String::as_str).unwrap_or_default();
    if first == "pwd" {
        return Ok(());
    }
    for token in tokens.iter().skip(1) {
        if token.starts_with('-') || token.is_empty() {
            continue;
        }
        if token == "." {
            continue;
        }
        if looks_like_path_argument(token) {
            let path = resolve_path(token)?;
            if sensitive_path(&path) {
                bail!("shell.execute path is blocked by the Agent Computer policy");
            }
            if hard_protected_root(&path) {
                bail!("shell.execute path is permanently protected");
            }
            enforce_path_policy(&path, allowed_roots)?;
        }
    }
    Ok(())
}

fn shell_tokens(command: &str) -> Vec<String> {
    command
        .split_whitespace()
        .map(|token| token.trim_matches('"').trim_matches('\'').to_string())
        .collect()
}

fn looks_like_path_argument(token: &str) -> bool {
    token.starts_with("/")
        || token.starts_with("~/")
        || token.starts_with("./")
        || token.starts_with("../")
        || token == "."
        || token.contains("/")
        || token.starts_with('.')
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

fn expand_home_alias(raw_path: &str) -> String {
    let value = raw_path.trim();
    if let Some(rest) = value.strip_prefix("~/") {
        if let Ok(home) = env::var("HOME") {
            return Path::new(&home).join(rest).to_string_lossy().to_string();
        }
    }
    value.to_string()
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
    bail!("shell.execute path is outside the Agent Computer policy");
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
        || normalized.contains("/.empyralis/state/vault")
        || normalized.contains("/.empyralis/state")
        || normalized.contains("/etc/empyralis")
        || normalized.contains("/var/lib/empyralis/agent-computer")
}

// ── Protected roots: ALWAYS enforced, NEVER bypassed by full_access ──
fn hard_protected_root(path: &Path) -> bool {
    let normalized = path.to_string_lossy().to_lowercase();
    let name = path
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
    use super::{
        command_touches_protected_path, enforce_shell_path_policy, hard_blocked_command,
        safe_shell_command, LOCAL_SYSTEM_INFO_PROBE_COMPACT,
    };

    #[test]
    fn safe_shell_command_allows_known_read_only_system_probe() {
        assert!(safe_shell_command(LOCAL_SYSTEM_INFO_PROBE_COMPACT));
    }

    #[test]
    fn safe_shell_command_still_blocks_unknown_compound_commands() {
        assert!(!safe_shell_command("pwd; echo unsafe"));
        assert!(!safe_shell_command("uname -a && whoami"));
    }

    #[test]
    fn safe_shell_policy_blocks_secret_paths() {
        assert!(enforce_shell_path_policy("cat .env", &[]).is_err());
    }

    // ── Hard-block tests (NEVER bypassed, even in full_access) ──

    #[test]
    fn hard_blocked_command_catches_rm_rf_root() {
        assert!(hard_blocked_command("rm -rf /"));
        assert!(hard_blocked_command("rm -rf /*"));
        assert!(hard_blocked_command("rm -fr /"));
        assert!(hard_blocked_command("rm -rf ~"));
        assert!(hard_blocked_command("rm -rf ~/"));
        // Normal space command should NOT be blocked
        assert!(!hard_blocked_command("ls -la /tmp"));
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
            assert!(
                hard_blocked_command(cmd),
                "expected blocked: {cmd}"
            );
        }
    }

    #[test]
    fn command_touches_protected_path_catches_vault_and_ssh() {
        // Credential vault — must NEVER be reachable
        assert!(command_touches_protected_path("rm -rf ~/.empyralis/state/vault"));
        assert!(command_touches_protected_path("rm -rf ~/.empyralis/state/vault/credentials.json"));
        assert!(command_touches_protected_path("cat ~/.empyralis/state/vault/key"));
        // SSH keys
        assert!(command_touches_protected_path("rm -rf ~/.ssh"));
        assert!(command_touches_protected_path("rm ~/.ssh/id_rsa"));
        // Worker config
        assert!(command_touches_protected_path("rm /etc/empyralis/agent-computer.env"));
        // Gateway state
        assert!(command_touches_protected_path("rm -rf /var/lib/empyralis/agent-computer"));
        // Runtime state
        assert!(command_touches_protected_path("rm -rf ~/.empyralis/state"));
        // Normal paths should NOT be blocked
        assert!(!command_touches_protected_path("rm -rf /tmp/scratch"));
        assert!(!command_touches_protected_path("rm ~/workspace/output.txt"));
    }

    #[test]
    fn command_touches_protected_path_catches_symlink_bypass() {
        // Agent creates ~/workspace/vault_link → ~/.empyralis/state/vault
        // then runs "rm -rf ~/workspace/vault_link/credentials.json"
        // The canonicalized path resolves to the vault — MUST be blocked.
        use std::fs;
        let root = std::env::temp_dir().join(format!(
            "empyralis-supervisor-shell-symlink-{}",
            uuid::Uuid::new_v4()
        ));
        fs::create_dir_all(&root).expect("create temp dir");

        let vault_dir = root.join(".empyralis").join("state").join("vault");
        fs::create_dir_all(&vault_dir).expect("create vault dir");
        let cred_file = vault_dir.join("credentials.json");
        fs::write(&cred_file, "secret").expect("write cred file");

        let workspace = root.join("workspace");
        fs::create_dir_all(&workspace).expect("create workspace");
        let link_path = workspace.join("vault_link");

        // Create symlink: workspace/vault_link → .empyralis/state/vault
        #[cfg(unix)]
        std::os::unix::fs::symlink(&vault_dir, &link_path).expect("create symlink");

        // Command targeting the symlink path should be blocked because
        // canonicalization resolves it to the real protected path
        let cmd = format!("rm -rf {}", link_path.join("credentials.json").to_string_lossy());
        assert!(
            command_touches_protected_path(&cmd),
            "symlink bypass via shell must be blocked: {cmd}"
        );

        // Clean up
        let _ = fs::remove_dir_all(&root);
    }
}

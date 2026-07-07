import fs from "fs";
import os from "os";
import path from "path";

// Ported from _archive/supervisor/empyralis-supervisor/src/capabilities/shell.rs
// and filesystem.rs (archived Phase U1, 9e70d4b4c — the isolation gap that got
// the Supervisor killed is unrelated to this policy layer, which is still
// good and worth keeping). These checks are the same in EVERY execution mode
// (sandbox and full_access) and are never bypassable — there is no trust tier
// that skips them. The old two-tier "trusted vs. safe-whitelist" allowlist
// from that Rust code is deliberately NOT ported: it belonged to an
// approval-gated design; the box-level sandbox/full_access toggle replaces it.

// ── Hard-blocked command patterns (NEVER bypassed, in any mode) ──
// Catastrophic commands whose execution causes irreversible destruction.
export const HARD_BLOCKED_COMMAND_PATTERNS: readonly string[] = [
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

// ── Hard-protected path markers (NEVER bypassed, in any mode) ──
// Credentials, pairings, runtime state, and worker config. Losing them
// forces re-login, re-pair, or re-setup.
export const HARD_PROTECTED_PATH_MARKERS: readonly string[] = [
  "/.empyralis/state/vault",
  "/.empyralis/state",
  "/.ssh",
  "/.gnupg",
  "/etc/empyralis",
  "/var/lib/empyralis/agent-computer",
  "/.orion-stack",
];

export interface PolicyBlock {
  code: "hard_blocked_command" | "hard_protected_path";
  message: string;
}

function trimMatches(value: string, ch: string): string {
  let start = 0;
  let end = value.length;
  while (start < end && value[start] === ch) {
    start += 1;
  }
  while (end > start && value[end - 1] === ch) {
    end -= 1;
  }
  return value.slice(start, end);
}

function cleanToken(token: string): string {
  return trimMatches(trimMatches(token, '"'), "'");
}

/**
 * Mirrors shell.rs's hard_blocked_command(): substring match on a
 * whitespace-collapsed, lowercased command string (not regex, not argv
 * parsing). Most patterns match as a prefix/substring anywhere. The
 * rm-prefixed root/home patterns have a carve-out: if immediately followed
 * by more path characters rather than a space or end-of-string (e.g.
 * "rm -rf ~/workspace"), they do NOT match — this is what lets workspace-
 * scoped deletes through while still blocking "rm -rf ~/" itself.
 */
export function hardBlockedCommand(command: string): boolean {
  const normalized = command.trim().split(/\s+/).join(" ").toLowerCase();
  if (!normalized) {
    return false;
  }
  for (const pattern of HARD_BLOCKED_COMMAND_PATTERNS) {
    const pos = normalized.indexOf(pattern);
    if (pos === -1) {
      continue;
    }
    const afterChar = normalized.charAt(pos + pattern.length);
    const isBoundary = afterChar === "" || afterChar === " ";
    if (isBoundary) {
      return true;
    }
    const isRootScopedRmPattern =
      pattern.startsWith("rm -") &&
      (pattern.endsWith(" /") || pattern.endsWith(" ~") || pattern.endsWith(" ~/") || pattern.endsWith(" ."));
    if (isRootScopedRmPattern) {
      continue;
    }
    return true;
  }
  return false;
}

/** Mirrors shell.rs's looks_like_path_argument(). */
function looksLikePathArgument(token: string): boolean {
  return (
    token.startsWith("/") ||
    token.startsWith("~/") ||
    token.startsWith("./") ||
    token.startsWith("../") ||
    token === "." ||
    token.includes("/") ||
    token.startsWith(".")
  );
}

/** Mirrors shell.rs's expand_home_alias(). */
function expandHomeAlias(rawPath: string): string {
  const value = rawPath.trim();
  if (value.startsWith("~/")) {
    const home = os.homedir();
    if (home) {
      return path.join(home, value.slice(2));
    }
  }
  return value;
}

/** Mirrors shell.rs's resolve_path(): expand ~/, join relative paths onto cwd. */
export function resolvePath(rawPath: string, cwd: string = process.cwd()): string {
  const trimmed = rawPath.trim();
  if (!trimmed) {
    throw new Error("path is required");
  }
  const expanded = expandHomeAlias(trimmed);
  return path.isAbsolute(expanded) ? expanded : path.join(cwd, expanded);
}

/**
 * Mirrors shell.rs's normalize_for_policy() / filesystem.rs's inlined
 * equivalent inside hard_protected_root(): canonicalize (resolving symlinks
 * and ./.. segments) if the path exists; if it doesn't exist yet (e.g. a
 * not-yet-created output file), canonicalize the parent directory and
 * rejoin the filename so the check still applies to where the file WOULD
 * land. Falls back to the raw resolved path if neither can be canonicalized.
 */
function canonicalizeBestEffort(targetPath: string): string {
  try {
    return fs.realpathSync(targetPath);
  } catch {
    const parent = path.dirname(targetPath);
    try {
      const canonicalParent = fs.realpathSync(parent);
      return path.join(canonicalParent, path.basename(targetPath));
    } catch {
      return targetPath;
    }
  }
}

function matchesProtectedPathMarker(normalizedLowerPath: string): boolean {
  return HARD_PROTECTED_PATH_MARKERS.some((marker) => normalizedLowerPath.includes(marker));
}

/**
 * Mirrors filesystem.rs's hard_protected_root(): a single resolved path
 * check, including the vault encryption-key-file special case (the key
 * file is literally named "key", no extension). Always enforced — this is
 * the check that applies regardless of sandbox vs. full_access mode.
 */
export function hardProtectedPath(rawPath: string, cwd?: string): boolean {
  let resolved: string;
  try {
    resolved = resolvePath(rawPath, cwd);
  } catch {
    return false;
  }
  const canonical = canonicalizeBestEffort(resolved);
  const normalized = canonical.toLowerCase();
  const baseName = path.basename(canonical).toLowerCase();
  if (
    baseName === "key" &&
    (normalized.includes("/.empyralis/state/vault") || normalized.includes("/.orion-stack"))
  ) {
    return true;
  }
  return matchesProtectedPathMarker(normalized);
}

/**
 * Mirrors shell.rs's command_touches_protected_path(): tokenizes a shell
 * command and checks both the raw token text (catches direct references
 * without needing filesystem access) and, for path-looking tokens that
 * resolve to an existing target, the symlink-resolved canonical form
 * (catches e.g. `ln -s ~/.empyralis/state/vault ~/workspace/link; rm link`).
 * Unlike hardProtectedPath(), there is no not-yet-exists fallback here —
 * matching the Rust original, which silently skips tokens it can't
 * canonicalize (the raw-string check above still applies to those).
 */
export function commandTouchesProtectedPath(command: string, cwd?: string): boolean {
  const tokens = command.split(/\s+/).filter(Boolean);
  for (const rawToken of tokens) {
    const cleaned = cleanToken(rawToken).toLowerCase();
    if (matchesProtectedPathMarker(cleaned)) {
      return true;
    }
    if (looksLikePathArgument(rawToken)) {
      try {
        const resolved = resolvePath(rawToken, cwd);
        const canonical = fs.realpathSync(resolved);
        if (matchesProtectedPathMarker(canonical.toLowerCase())) {
          return true;
        }
      } catch {
        // Path doesn't resolve (doesn't exist / broken symlink) — matches
        // the Rust original's `if let Ok(canonical) = fs::canonicalize(...)`
        // silently skipping unresolvable tokens here.
      }
    }
  }
  return false;
}

/**
 * Full hard-block policy check for a shell command. Call this BEFORE
 * spawning anything, in every execution mode.
 */
export function checkShellCommandPolicy(command: string, cwd?: string): PolicyBlock | null {
  if (hardBlockedCommand(command)) {
    return {
      code: "hard_blocked_command",
      message:
        "shell.execute command is permanently blocked — it could cause irreversible destruction of the runtime environment",
    };
  }
  if (commandTouchesProtectedPath(command, cwd)) {
    return {
      code: "hard_protected_path",
      message: "shell.execute path is permanently protected — it contains credentials, pairings, or runtime state",
    };
  }
  return null;
}

/**
 * Full hard-block policy check for a single filesystem path (read/write/
 * append/delete). Call this BEFORE touching the filesystem, in every
 * execution mode.
 */
export function checkFilesystemPathPolicy(rawPath: string, cwd?: string): PolicyBlock | null {
  if (hardProtectedPath(rawPath, cwd)) {
    return {
      code: "hard_protected_path",
      message: "filesystem path is permanently protected — it contains credentials, pairings, or runtime state",
    };
  }
  return null;
}

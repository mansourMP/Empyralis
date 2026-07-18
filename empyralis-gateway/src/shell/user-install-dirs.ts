import fs from "fs";
import os from "os";
import path from "path";

// Shared by every place in the Gateway that needs to find a CLI binary by
// name: health/service-inventory.ts's passive claude/codex detection,
// llm/cli-login-session.ts's sign-in spawn, and llm/cli-installer.ts's
// install + post-install verification. Single source of truth so a fix here
// reaches all of them, instead of drifting out of sync — which is exactly
// how this bug shipped independently in more than one of those files: each
// had its own small, PATH-only lookup, "duplicated, not imported" for module
// independence, and only one of them got the ~/.local/bin fix at first.
//
// The Gateway's own process PATH (whatever launchd/systemd/the parent
// process handed it) is frequently narrower than an interactive login
// shell's — that's the literal reason `claude`/`codex` can report "Not
// installed" (or "not_installed"/npm "still not on PATH" from the
// login/install flows) on a box where `which claude` in a Terminal finds it
// just fine. Confirmed real case: Claude Code's native installer
// (`curl ... | bash`) puts the binary at `~/.local/bin/claude`, which is on
// a login shell's PATH (via .zshrc/.bashrc/.profile) but NOT on the
// Gateway's own process PATH. Rather than trying to reconstruct the user's
// full shell PATH (fragile — differs per shell/profile/OS), fall back to a
// fixed set of the standard locations user-level CLI installers actually
// use on macOS + Linux, in ADDITION to (never instead of) PATH. Windows is
// intentionally excluded — none of these are meaningful conventions there.
export function standardUserInstallDirs(env: NodeJS.ProcessEnv, platform: NodeJS.Platform): string[] {
  if (platform === "win32") {
    return [];
  }
  const home = String(env.HOME || env.USERPROFILE || "").trim() || os.homedir();
  const npmConfigPrefix = String(env.NPM_CONFIG_PREFIX || env.npm_config_prefix || "").trim();
  const dirs = [
    // Claude Code's native installer — the confirmed real-world miss this
    // fix exists for.
    path.join(home, ".local", "bin"),
    // A common user-level npm global-prefix convention (`npm config set
    // prefix ~/.npm-global`).
    path.join(home, ".npm-global", "bin"),
    // npm's actual configured global prefix for this process, if set via
    // env — covers `npm install -g @openai/codex` / `npm install -g
    // @anthropic-ai/claude-code` landing outside all of the above. Ranked
    // ahead of the hardcoded guesses below: an explicit configured prefix is
    // a stronger signal than a generic default location.
    ...(npmConfigPrefix ? [path.join(npmConfigPrefix, "bin")] : []),
    // Homebrew's default prefixes — Apple Silicon and Intel macOS
    // respectively (also the generic Linuxbrew/local-install path for
    // /usr/local/bin).
    "/opt/homebrew/bin",
    "/usr/local/bin",
    // Last-resort fallback proxy for npm's global prefix when it's not set
    // via env: npm installs global bins into the same directory the active
    // `node` binary lives in (true for nvm, Homebrew Node, and most other
    // installs). Reading the Gateway's own execPath costs nothing and needs
    // no subprocess, unlike shelling out to `npm config get prefix`.
    path.dirname(process.execPath),
  ];
  return [...new Set(dirs)];
}

/** Resolves `command` to an absolute path, checking (in order): an absolute
 *  or explicitly-pathed input as-is, then PATH, then standardUserInstallDirs
 *  as a fallback — same shape as a shell's own lookup, just wider. Returns
 *  null (never throws) when nothing matches; callers translate that into
 *  their own "not installed" signal. */
export function resolveCommandPath(command: string, env: NodeJS.ProcessEnv, platform: NodeJS.Platform): string | null {
  const candidates: string[] = [];
  if (path.isAbsolute(command) || command.includes("/") || command.includes("\\")) {
    candidates.push(command);
  } else {
    const pathValue = env.PATH || "";
    const extensions = platform === "win32"
      ? String(env.PATHEXT || ".EXE;.CMD;.BAT;.COM").split(";").filter(Boolean)
      : [""];
    // Known standard user-install locations, checked in ADDITION to PATH —
    // see standardUserInstallDirs' doc comment. A CLI installed there is now
    // found exactly as reliably as one on PATH; fs.existsSync below is cheap
    // and a directory that's already on PATH is just a harmless repeat stat.
    const searchDirs = [
      ...pathValue.split(path.delimiter).filter(Boolean),
      ...standardUserInstallDirs(env, platform),
    ];
    for (const directory of searchDirs) {
      for (const extension of extensions) {
        candidates.push(path.join(directory, `${command}${extension}`));
      }
    }
  }
  for (const candidate of candidates) {
    try {
      if (fs.existsSync(candidate)) {
        return candidate;
      }
    } catch {
      // Ignore inaccessible PATH entries.
    }
  }
  return null;
}

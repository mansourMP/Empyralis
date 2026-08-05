import test from "node:test";
import assert from "node:assert/strict";
import fs from "fs";
import os from "os";
import path from "path";
import { mkdtemp, rm, mkdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";

import { buildFastPassiveInventorySnapshot, collectPassiveInventorySnapshot } from "../health/service-inventory";

/** Bare-minimum command router shared by the claude_cli auth-detection tests
 *  below: enough for collectPassiveInventorySnapshot's other probes (postgres,
 *  docker, ollama, codex_cli, gpu) to resolve to a quiet "not present" instead
 *  of throwing, so each test's assertions can focus on claude_cli alone. */
function baseCommandExists(extra: Record<string, string> = {}) {
  return (command: string): string | null => {
    if (command in extra) return extra[command];
    if (command === "claude") return "/usr/bin/claude";
    return null;
  };
}
async function baseRunCommand(command: string): Promise<{ exitCode: number | null; stdout: string; stderr: string }> {
  if (command.endsWith("claude")) return { exitCode: 0, stdout: "2.1.214 (Claude Code)", stderr: "" };
  return { exitCode: 1, stdout: "", stderr: "not present" };
}

test("passive service inventory detects services without enabling execution", async () => {
  const snapshot = await collectPassiveInventorySnapshot({
    requestedCapabilities: ["shell.execute", "screenshot.capture"],
    deps: {
      platform: "linux",
      arch: "x64",
      release: "6.0-test",
      hostname: "agent-box",
      now: () => new Date("2026-05-29T00:00:00Z"),
      commandExists: (command) => {
        if (command === "pg_isready") return "/usr/bin/pg_isready";
        if (command === "docker") return "/usr/bin/docker";
        return null;
      },
      runCommand: async (command) => {
        if (command.endsWith("pg_isready")) return { exitCode: 0, stdout: "", stderr: "" };
        if (command.endsWith("docker")) return { exitCode: 1, stdout: "", stderr: "daemon unavailable" };
        return { exitCode: 1, stdout: "", stderr: "unexpected" };
      },
      httpGetJson: async () => ({ ok: true, status: 200, body: { models: [{ name: "llama" }] } }),
    },
  });

  const byId = Object.fromEntries(snapshot.service_inventory.map((item) => [item.id, item]));
  assert.equal(snapshot.native_runtime.desktop_session, "user_session");
  assert.equal(snapshot.native_runtime.system_service_mode, false);
  assert.equal(byId.postgres.status, "ready");
  assert.equal(byId.docker.status, "offline");
  assert.equal(byId.ollama.status, "ready");
  assert.equal(byId.codex_cli.status, "missing");
  assert.equal(byId.gpu.status, "unknown");
  for (const item of snapshot.service_inventory) {
    assert.equal(item.passive, true);
    assert.equal(item.execution_enabled, false);
  }
  assert.deepEqual(snapshot.capability_readiness.requested, ["shell.execute", "screenshot.capture"]);
  // shell.execute is gated on the shell_sandbox permission, which is only
  // granted when Docker reads ready — this mock reports Docker offline, so
  // shell.execute is correctly blocked here; screenshot.capture is unrelated
  // (screen_recording permission) and stays ready.
  assert.deepEqual(snapshot.capability_readiness.ready, ["screenshot.capture"]);
  assert.deepEqual(snapshot.capability_readiness.blocked, ["shell.execute"]);
  assert.equal(snapshot.capability_readiness.permission_states["shell.execute"].state, "restricted");
  assert.equal(snapshot.capability_readiness.service_statuses.postgres, "ready");
  // Not passed in this call's options — must default to false, never
  // silently coerced to true. See the two dedicated tests below for the
  // opted-in case.
  assert.equal(snapshot.capability_readiness.shell_full_access_locally_enabled, false);
});

// Hardware-readiness gap (MAN): the control plane has only ever known what
// runtime_access_mode IT authorized server-side, never whether the box
// operator's own EMPYRALIS_GATEWAY_SHELL_FULL_ACCESS_ENABLED opt-in is
// actually on right now. These two tests cover both real snapshot builders
// (not a hand-rolled fixture) so a regression here — the flag silently
// getting dropped or defaulted wrong on the path ws-client.ts actually
// calls — fails loudly.
test("collectPassiveInventorySnapshot reports the box operator's live full_access opt-in", async () => {
  const snapshotEnabled = await collectPassiveInventorySnapshot({
    requestedCapabilities: [],
    shellFullAccessLocallyEnabled: true,
    deps: {
      platform: "linux",
      arch: "x64",
      release: "6.0-test",
      hostname: "agent-box",
      now: () => new Date("2026-05-29T00:00:00Z"),
      commandExists: () => null,
      runCommand: async () => ({ exitCode: 1, stdout: "", stderr: "not present" }),
      httpGetJson: async () => ({ ok: false, status: 0, error: "not reachable" }),
    },
  });
  assert.equal(snapshotEnabled.capability_readiness.shell_full_access_locally_enabled, true);

  const snapshotDisabled = await collectPassiveInventorySnapshot({
    requestedCapabilities: [],
    shellFullAccessLocallyEnabled: false,
    deps: {
      platform: "linux",
      arch: "x64",
      release: "6.0-test",
      hostname: "agent-box",
      now: () => new Date("2026-05-29T00:00:01Z"),
      commandExists: () => null,
      runCommand: async () => ({ exitCode: 1, stdout: "", stderr: "not present" }),
      httpGetJson: async () => ({ ok: false, status: 0, error: "not reachable" }),
    },
  });
  assert.equal(snapshotDisabled.capability_readiness.shell_full_access_locally_enabled, false);
});

test("fast passive inventory snapshot avoids service probes for heartbeat liveness", () => {
  const snapshot = buildFastPassiveInventorySnapshot({
    requestedCapabilities: ["shell.execute", "screenshot.capture"],
    localRunnerReady: true,
    shellFullAccessLocallyEnabled: true,
    deps: {
      platform: "linux",
      arch: "x64",
      release: "6.0-test",
      hostname: "agent-box",
      now: () => new Date("2026-05-29T00:00:00Z"),
      commandExists: () => {
        throw new Error("fast snapshot must not run command probes");
      },
      runCommand: async () => {
        throw new Error("fast snapshot must not run command probes");
      },
      httpGetJson: async () => {
        throw new Error("fast snapshot must not run HTTP probes");
      },
    },
  });

  assert.equal(snapshot.native_runtime.hostname, "agent-box");
  assert.deepEqual(snapshot.capability_readiness.requested, ["shell.execute", "screenshot.capture"]);
  assert.deepEqual(snapshot.service_inventory.map((item) => item.id), ["local_runner", "desktop_permissions"]);
  assert.equal(snapshot.capability_readiness.service_statuses.local_runner, "ready");
  // The fast/no-probe path (used on the very first heartbeat before the
  // slower probe suite has resolved — see ws-client.ts's sendHeartbeat)
  // must still carry the operator's real opt-in through, not just the
  // slower collectPassiveInventorySnapshot() path covered above.
  assert.equal(snapshot.capability_readiness.shell_full_access_locally_enabled, true);
});

test("local runner readiness blocks supervisor-backed capabilities", () => {
  // screenshot.capture and computer_control.click still depend on the old
  // local-runner/supervisor path (desktop control remains out of scope).
  // shell.execute does NOT anymore — it has its own independent,
  // Docker-gated executor now (see the shell_sandbox tests below) — so it's
  // deliberately not used as the example capability in this test.
  const snapshot = buildFastPassiveInventorySnapshot({
    requestedCapabilities: ["computer_control.click", "screenshot.capture"],
    localRunnerReady: false,
    deps: {
      platform: "linux",
      arch: "x64",
      release: "6.0-test",
      hostname: "agent-box",
      now: () => new Date("2026-05-29T00:00:00Z"),
    },
  });

  assert.deepEqual(snapshot.capability_readiness.ready, []);
  assert.deepEqual(snapshot.capability_readiness.blocked, ["computer_control.click", "screenshot.capture"]);
  assert.equal(snapshot.capability_readiness.service_statuses.local_runner, "offline");
});

test("shell_sandbox capabilities are no longer forced-blocked by local runner health", () => {
  // Confirms the collision fix directly: shell.execute must NOT be blocked
  // just because localRunnerReady is false (checkLocalRunnerHealth() in
  // ws-client.ts hardcodes false always) — its readiness now comes from the
  // shell_sandbox permission (Docker), which this test grants explicitly via
  // the env override, independent of local-runner health entirely.
  const snapshot = buildFastPassiveInventorySnapshot({
    requestedCapabilities: ["shell.execute", "screenshot.capture"],
    localRunnerReady: false,
    deps: {
      platform: "linux",
      arch: "x64",
      release: "6.0-test",
      hostname: "agent-box",
      now: () => new Date("2026-05-29T00:00:00Z"),
      env: { EMPYRALIS_AGENT_COMPUTER_PERMISSION_SHELL_SANDBOX: "granted" },
    },
  });

  assert.ok(snapshot.capability_readiness.ready.includes("shell.execute"));
  assert.equal(snapshot.capability_readiness.blocked.includes("shell.execute"), false);
});

// ---- CLI detection beyond the Gateway's own process PATH -----------------
// The Gateway's process PATH (launchd/systemd-supplied) is frequently
// narrower than an interactive login shell's — confirmed real case: Claude
// Code's native installer puts the binary at `~/.local/bin/claude`, which
// `which claude` finds from a Terminal (a login shell's PATH, via .zshrc et
// al) but the Gateway process never sees. These tests deliberately do NOT
// override `commandExists` in deps — leaving it unset means
// collectPassiveInventorySnapshot falls through to the real, production
// defaultCommandExists()/standardUserInstallDirs() in service-inventory.ts,
// so they exercise the actual lookup logic end-to-end, not a test double of
// it. `runCommand`/`httpGetJson` ARE stubbed — they only affect the
// separate installed-vs-authenticated/liveness signal, never whether the
// binary is found in the first place.
const stubRunCommand = async () => ({ exitCode: 0, stdout: "", stderr: "" });
const stubHttpGetJson = async () => ({ ok: false, status: 0, error: "not running" });

test("claude_cli is detected in ~/.local/bin even when it is not on PATH (macOS)", async (t) => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "empyralis-cli-detect-home-"));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const localBin = path.join(home, ".local", "bin");
  fs.mkdirSync(localBin, { recursive: true });
  const claudePath = path.join(localBin, "claude");
  fs.writeFileSync(claudePath, "#!/bin/sh\necho fake-claude\n", { mode: 0o755 });

  const snapshot = await collectPassiveInventorySnapshot({
    deps: {
      platform: "darwin",
      arch: "arm64",
      release: "23.0-test",
      hostname: "mac-box",
      now: () => new Date("2026-07-18T00:00:00Z"),
      // Deliberately narrow — does NOT include localBin — reproducing the
      // real Gateway-process-PATH gap, not a full interactive shell PATH.
      env: { HOME: home, PATH: "/usr/bin:/bin" },
      runCommand: stubRunCommand,
      httpGetJson: stubHttpGetJson,
    },
  });

  const byId = Object.fromEntries(snapshot.service_inventory.map((item) => [item.id, item]));
  assert.equal(byId.claude_cli.detected, true, "claude CLI in ~/.local/bin must be detected");
  assert.notEqual(byId.claude_cli.status, "missing", "an installed-but-unlisted-on-PATH CLI must not report missing");
  assert.equal(byId.claude_cli.metadata?.installed, true);
  assert.equal(
    byId.claude_cli.metadata?.path,
    claudePath,
    "must resolve to the ~/.local/bin copy specifically, proving it was NOT found via PATH",
  );
});

test("codex_cli is detected in ~/.local/bin even when it is not on PATH (Linux)", async (t) => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "empyralis-cli-detect-home-"));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const localBin = path.join(home, ".local", "bin");
  fs.mkdirSync(localBin, { recursive: true });
  const codexPath = path.join(localBin, "codex");
  fs.writeFileSync(codexPath, "#!/bin/sh\necho fake-codex\n", { mode: 0o755 });

  const snapshot = await collectPassiveInventorySnapshot({
    deps: {
      platform: "linux",
      arch: "x64",
      release: "6.0-test",
      hostname: "linux-box",
      now: () => new Date("2026-07-18T00:00:00Z"),
      env: { HOME: home, PATH: "/usr/bin:/bin" },
      runCommand: stubRunCommand,
      httpGetJson: stubHttpGetJson,
    },
  });

  const byId = Object.fromEntries(snapshot.service_inventory.map((item) => [item.id, item]));
  assert.equal(byId.codex_cli.detected, true, "codex CLI in ~/.local/bin must be detected");
  assert.notEqual(byId.codex_cli.status, "missing", "an installed-but-unlisted-on-PATH CLI must not report missing");
  assert.equal(byId.codex_cli.metadata?.installed, true);
  assert.equal(
    byId.codex_cli.metadata?.path,
    codexPath,
    "must resolve to the ~/.local/bin copy specifically, proving it was NOT found via PATH",
  );
});

test("codex_cli is detected via NPM_CONFIG_PREFIX's bin dir even when it is not on PATH or ~/.local/bin", async (t) => {
  const prefixDir = fs.mkdtempSync(path.join(os.tmpdir(), "empyralis-cli-detect-npmprefix-"));
  t.after(() => fs.rmSync(prefixDir, { recursive: true, force: true }));
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "empyralis-cli-detect-home-"));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const npmBin = path.join(prefixDir, "bin");
  fs.mkdirSync(npmBin, { recursive: true });
  const codexPath = path.join(npmBin, "codex");
  fs.writeFileSync(codexPath, "#!/bin/sh\necho fake-codex\n", { mode: 0o755 });

  const snapshot = await collectPassiveInventorySnapshot({
    deps: {
      platform: "linux",
      arch: "x64",
      release: "6.0-test",
      hostname: "linux-box",
      now: () => new Date("2026-07-18T00:00:00Z"),
      // HOME is a fresh empty temp dir — no ~/.local/bin or ~/.npm-global/bin
      // copy exists — isolating this test to the NPM_CONFIG_PREFIX fallback
      // specifically, not the ~/.local/bin one covered above.
      env: { HOME: home, PATH: "/usr/bin:/bin", NPM_CONFIG_PREFIX: prefixDir },
      runCommand: stubRunCommand,
      httpGetJson: stubHttpGetJson,
    },
  });

  const byId = Object.fromEntries(snapshot.service_inventory.map((item) => [item.id, item]));
  assert.equal(byId.codex_cli.detected, true, "codex CLI in npm's configured global prefix must be detected");
  assert.notEqual(byId.codex_cli.status, "missing");
  assert.equal(byId.codex_cli.metadata?.path, codexPath);
});

// ---- Claude Code auth-presence detection ----------------------------------
// Covers the reliable, OS-independent mechanism (CLAUDE_CODE_OAUTH_TOKEN /
// ANTHROPIC_API_KEY / the credentials file Anthropic documents at
// https://code.claude.com/docs/en/authentication#credential-management) that
// probeClaudeCli uses to flip claude_cli from "degraded" (installed, not
// signed in) to "ready" — this is what the Hardware page's status pill and
// the llm_runtime.claude_code capability gate both key off of.

test("claude_cli reports ready when CLAUDE_CODE_OAUTH_TOKEN is present", async () => {
  const snapshot = await collectPassiveInventorySnapshot({
    deps: {
      platform: "linux",
      now: () => new Date("2026-07-18T00:00:00Z"),
      commandExists: baseCommandExists(),
      runCommand: baseRunCommand,
      httpGetJson: async () => ({ ok: false, status: 0 }),
      env: { HOME: "/nonexistent-home-for-this-test", CLAUDE_CODE_OAUTH_TOKEN: "sk-ant-oat01-not-a-real-token" },
    },
  });
  const claudeItem = snapshot.service_inventory.find((item) => item.id === "claude_cli");
  assert.ok(claudeItem, "claude_cli must appear in the inventory");
  assert.equal(claudeItem?.status, "ready");
  assert.equal(claudeItem?.metadata?.authenticated, true);
  assert.equal(claudeItem?.metadata?.installed, true);
});

test("claude_cli reports degraded (installed, not signed in) when no auth signal is present", async () => {
  const snapshot = await collectPassiveInventorySnapshot({
    deps: {
      platform: "linux",
      now: () => new Date("2026-07-18T00:00:00Z"),
      commandExists: baseCommandExists(),
      runCommand: baseRunCommand,
      httpGetJson: async () => ({ ok: false, status: 0 }),
      env: { HOME: "/nonexistent-home-for-this-test" },
    },
  });
  const claudeItem = snapshot.service_inventory.find((item) => item.id === "claude_cli");
  assert.equal(claudeItem?.status, "degraded");
  assert.equal(claudeItem?.metadata?.authenticated, false);
  assert.equal(claudeItem?.metadata?.installed, true);
});

test("claude_cli reports missing when the binary isn't on PATH", async () => {
  const snapshot = await collectPassiveInventorySnapshot({
    deps: {
      platform: "linux",
      now: () => new Date("2026-07-18T00:00:00Z"),
      commandExists: () => null,
      runCommand: baseRunCommand,
      httpGetJson: async () => ({ ok: false, status: 0 }),
      env: { HOME: "/nonexistent-home-for-this-test" },
    },
  });
  const claudeItem = snapshot.service_inventory.find((item) => item.id === "claude_cli");
  assert.equal(claudeItem?.status, "missing");
  assert.equal(claudeItem?.metadata?.installed, false);
  assert.equal(claudeItem?.metadata?.authenticated, false);
});

test("claude_cli reports ready when ~/.claude/.credentials.json exists (Linux/Windows storage)", async (t) => {
  const home = await mkdtemp(path.join(tmpdir(), "empyralis-claude-cred-"));
  t.after(() => rm(home, { recursive: true, force: true }));
  await mkdir(path.join(home, ".claude"), { recursive: true });
  await writeFile(path.join(home, ".claude", ".credentials.json"), JSON.stringify({ note: "test fixture, not a real credential" }));

  const snapshot = await collectPassiveInventorySnapshot({
    deps: {
      platform: "linux",
      now: () => new Date("2026-07-18T00:00:00Z"),
      commandExists: baseCommandExists(),
      runCommand: baseRunCommand,
      httpGetJson: async () => ({ ok: false, status: 0 }),
      env: { HOME: home },
    },
  });
  const claudeItem = snapshot.service_inventory.find((item) => item.id === "claude_cli");
  assert.equal(claudeItem?.status, "ready");
  assert.equal(claudeItem?.metadata?.authenticated, true);
});

test("claude_cli respects CLAUDE_CONFIG_DIR for the credentials file on Linux", async (t) => {
  const home = await mkdtemp(path.join(tmpdir(), "empyralis-claude-home-"));
  const configDir = await mkdtemp(path.join(tmpdir(), "empyralis-claude-config-"));
  t.after(async () => {
    await rm(home, { recursive: true, force: true });
    await rm(configDir, { recursive: true, force: true });
  });
  // Nothing under the default ~/.claude location — only under CLAUDE_CONFIG_DIR.
  await writeFile(path.join(configDir, ".credentials.json"), JSON.stringify({ note: "test fixture, not a real credential" }));

  const snapshot = await collectPassiveInventorySnapshot({
    deps: {
      platform: "linux",
      now: () => new Date("2026-07-18T00:00:00Z"),
      commandExists: baseCommandExists(),
      runCommand: baseRunCommand,
      httpGetJson: async () => ({ ok: false, status: 0 }),
      env: { HOME: home, CLAUDE_CONFIG_DIR: configDir },
    },
  });
  const claudeItem = snapshot.service_inventory.find((item) => item.id === "claude_cli");
  assert.equal(claudeItem?.status, "ready", "CLAUDE_CONFIG_DIR override must be honored, not just the default ~/.claude path");
  assert.equal(claudeItem?.metadata?.authenticated, true);
});

test("claude_cli does NOT apply the CLAUDE_CONFIG_DIR override on macOS (Keychain-only storage)", async (t) => {
  const home = await mkdtemp(path.join(tmpdir(), "empyralis-claude-mac-home-"));
  const configDir = await mkdtemp(path.join(tmpdir(), "empyralis-claude-mac-config-"));
  t.after(async () => {
    await rm(home, { recursive: true, force: true });
    await rm(configDir, { recursive: true, force: true });
  });
  await writeFile(path.join(configDir, ".credentials.json"), JSON.stringify({ note: "test fixture, not a real credential" }));

  const snapshot = await collectPassiveInventorySnapshot({
    deps: {
      platform: "darwin",
      now: () => new Date("2026-07-18T00:00:00Z"),
      commandExists: baseCommandExists({ security: "/usr/bin/security" }),
      runCommand: async (command, args) => {
        if (command.endsWith("security")) return { exitCode: 1, stdout: "", stderr: "The specified item could not be found in the keychain." };
        return baseRunCommand(command);
      },
      httpGetJson: async () => ({ ok: false, status: 0 }),
      env: { HOME: home, CLAUDE_CONFIG_DIR: configDir },
    },
  });
  const claudeItem = snapshot.service_inventory.find((item) => item.id === "claude_cli");
  assert.equal(claudeItem?.status, "degraded", "macOS always uses the Keychain; a Linux/Windows-only override must not leak in");
  assert.equal(claudeItem?.metadata?.authenticated, false);
});

test("claude_cli reports ready on macOS when the Keychain item exists (metadata-only check, no secret read)", async (t) => {
  const home = await mkdtemp(path.join(tmpdir(), "empyralis-claude-mac-kc-"));
  t.after(() => rm(home, { recursive: true, force: true }));
  let securityArgs: string[] | null = null;

  const snapshot = await collectPassiveInventorySnapshot({
    deps: {
      platform: "darwin",
      now: () => new Date("2026-07-18T00:00:00Z"),
      commandExists: baseCommandExists({ security: "/usr/bin/security" }),
      runCommand: async (command, args) => {
        if (command.endsWith("security")) {
          securityArgs = args;
          return { exitCode: 0, stdout: "", stderr: "" };
        }
        return baseRunCommand(command);
      },
      httpGetJson: async () => ({ ok: false, status: 0 }),
      env: { HOME: home },
    },
  });
  const claudeItem = snapshot.service_inventory.find((item) => item.id === "claude_cli");
  assert.equal(claudeItem?.status, "ready");
  assert.equal(claudeItem?.metadata?.authenticated, true);
  // The probe must never pass -w (the flag that would return the secret itself).
  assert.ok(securityArgs, "the security CLI must have been invoked");
  assert.ok(!(securityArgs as unknown as string[]).includes("-w"), "must never request the secret value, existence only");
});

// ---- xAI Grok Build / Cursor CLI addition ---------------------------------
// Same installed-vs-authenticated distinction as claude_cli/codex_cli above —
// this is what flips grok_cli/cursor_cli service_inventory rows from
// "degraded" (installed, not signed in) to "ready", which is the SAME signal
// _cli_subscription_readiness_reason (Python) and gatewayRuntimeState
// (frontend) key off to decide whether a turn — or an "already ready, reuse
// it" recommendation — is safe.

function baseCommandExistsGrok(extra: Record<string, string> = {}) {
  return (command: string): string | null => {
    if (command in extra) return extra[command];
    if (command === "grok") return "/usr/bin/grok";
    return null;
  };
}
async function baseRunCommandGrok(command: string): Promise<{ exitCode: number | null; stdout: string; stderr: string }> {
  if (command.endsWith("grok")) return { exitCode: 0, stdout: "0.12.3 (Grok Build)", stderr: "" };
  return { exitCode: 1, stdout: "", stderr: "not present" };
}

test("grok_cli reports missing when the binary isn't on PATH", async () => {
  const snapshot = await collectPassiveInventorySnapshot({
    deps: {
      platform: "linux",
      now: () => new Date("2026-07-24T00:00:00Z"),
      commandExists: () => null,
      runCommand: baseRunCommandGrok,
      httpGetJson: async () => ({ ok: false, status: 0 }),
      env: { HOME: "/nonexistent-home-for-this-test" },
    },
  });
  const grokItem = snapshot.service_inventory.find((item) => item.id === "grok_cli");
  assert.equal(grokItem?.status, "missing");
  assert.equal(grokItem?.metadata?.installed, false);
  assert.equal(grokItem?.metadata?.authenticated, false);
});

test("grok_cli reports degraded (installed, not signed in) when neither ~/.grok/auth.json nor XAI_API_KEY is present", async () => {
  const snapshot = await collectPassiveInventorySnapshot({
    deps: {
      platform: "linux",
      now: () => new Date("2026-07-24T00:00:00Z"),
      commandExists: baseCommandExistsGrok(),
      runCommand: baseRunCommandGrok,
      httpGetJson: async () => ({ ok: false, status: 0 }),
      env: { HOME: "/nonexistent-home-for-this-test" },
    },
  });
  const grokItem = snapshot.service_inventory.find((item) => item.id === "grok_cli");
  assert.equal(grokItem?.status, "degraded");
  assert.equal(grokItem?.metadata?.installed, true);
  assert.equal(grokItem?.metadata?.authenticated, false);
});

test("grok_cli reports ready when XAI_API_KEY is present", async () => {
  const snapshot = await collectPassiveInventorySnapshot({
    deps: {
      platform: "linux",
      now: () => new Date("2026-07-24T00:00:00Z"),
      commandExists: baseCommandExistsGrok(),
      runCommand: baseRunCommandGrok,
      httpGetJson: async () => ({ ok: false, status: 0 }),
      env: { HOME: "/nonexistent-home-for-this-test", XAI_API_KEY: "xai-not-a-real-key" },
    },
  });
  const grokItem = snapshot.service_inventory.find((item) => item.id === "grok_cli");
  assert.equal(grokItem?.status, "ready");
  assert.equal(grokItem?.metadata?.authenticated, true);
});

test("grok_cli reports ready when ~/.grok/auth.json exists (a completed `grok login --device-auth` session)", async (t) => {
  const home = await mkdtemp(path.join(tmpdir(), "empyralis-grok-cred-"));
  t.after(() => rm(home, { recursive: true, force: true }));
  await mkdir(path.join(home, ".grok"), { recursive: true });
  await writeFile(path.join(home, ".grok", "auth.json"), "{}");

  const snapshot = await collectPassiveInventorySnapshot({
    deps: {
      platform: "linux",
      now: () => new Date("2026-07-24T00:00:00Z"),
      commandExists: baseCommandExistsGrok(),
      runCommand: baseRunCommandGrok,
      httpGetJson: async () => ({ ok: false, status: 0 }),
      env: { HOME: home },
    },
  });
  const grokItem = snapshot.service_inventory.find((item) => item.id === "grok_cli");
  assert.equal(grokItem?.status, "ready");
  assert.equal(grokItem?.metadata?.authenticated, true);
});

function baseCommandExistsCursor(extra: Record<string, string> = {}) {
  return (command: string): string | null => {
    if (command in extra) return extra[command];
    if (command === "cursor-agent") return "/usr/bin/cursor-agent";
    return null;
  };
}

test("cursor_cli reports missing when the binary isn't on PATH", async () => {
  const snapshot = await collectPassiveInventorySnapshot({
    deps: {
      platform: "linux",
      now: () => new Date("2026-07-24T00:00:00Z"),
      commandExists: () => null,
      runCommand: async () => ({ exitCode: 1, stdout: "", stderr: "not present" }),
      httpGetJson: async () => ({ ok: false, status: 0 }),
      env: { HOME: "/nonexistent-home-for-this-test" },
    },
  });
  const cursorItem = snapshot.service_inventory.find((item) => item.id === "cursor_cli");
  assert.equal(cursorItem?.status, "missing");
  assert.equal(cursorItem?.metadata?.installed, false);
  assert.equal(cursorItem?.metadata?.authenticated, false);
});

test("cursor_cli reports ready when CURSOR_API_KEY is present (no `status` probe needed)", async () => {
  let statusInvoked = false;
  const snapshot = await collectPassiveInventorySnapshot({
    deps: {
      platform: "linux",
      now: () => new Date("2026-07-24T00:00:00Z"),
      commandExists: baseCommandExistsCursor(),
      runCommand: async (command, args) => {
        if (args?.[0] === "status") statusInvoked = true;
        if (command.endsWith("cursor-agent")) return { exitCode: 0, stdout: "2026.07.23", stderr: "" };
        return { exitCode: 1, stdout: "", stderr: "not present" };
      },
      httpGetJson: async () => ({ ok: false, status: 0 }),
      env: { HOME: "/nonexistent-home-for-this-test", CURSOR_API_KEY: "not-a-real-key" },
    },
  });
  const cursorItem = snapshot.service_inventory.find((item) => item.id === "cursor_cli");
  assert.equal(cursorItem?.status, "ready");
  assert.equal(cursorItem?.metadata?.authenticated, true);
  assert.equal(statusInvoked, false, "CURSOR_API_KEY alone should short-circuit the extra `status` probe");
});

test("cursor_cli reports degraded (installed, not signed in) when `cursor-agent status` reports the documented not-authenticated wording", async () => {
  const snapshot = await collectPassiveInventorySnapshot({
    deps: {
      platform: "linux",
      now: () => new Date("2026-07-24T00:00:00Z"),
      commandExists: baseCommandExistsCursor(),
      runCommand: async (command, args) => {
        if (command.endsWith("cursor-agent") && args?.[0] === "status") {
          return { exitCode: 1, stdout: "", stderr: "Not authenticated. Run 'agent login' to sign in." };
        }
        if (command.endsWith("cursor-agent")) return { exitCode: 0, stdout: "2026.07.23", stderr: "" };
        return { exitCode: 1, stdout: "", stderr: "not present" };
      },
      httpGetJson: async () => ({ ok: false, status: 0 }),
      env: { HOME: "/nonexistent-home-for-this-test" },
    },
  });
  const cursorItem = snapshot.service_inventory.find((item) => item.id === "cursor_cli");
  assert.equal(cursorItem?.status, "degraded");
  assert.equal(cursorItem?.metadata?.installed, true);
  assert.equal(cursorItem?.metadata?.authenticated, false);
});

test("cursor_cli reports ready when `cursor-agent status` exits 0 with no not-authenticated wording (best-effort — no officially published status schema exists to parse instead)", async () => {
  const snapshot = await collectPassiveInventorySnapshot({
    deps: {
      platform: "linux",
      now: () => new Date("2026-07-24T00:00:00Z"),
      commandExists: baseCommandExistsCursor(),
      runCommand: async (command, args) => {
        if (command.endsWith("cursor-agent") && args?.[0] === "status") {
          return { exitCode: 0, stdout: "Logged in as owner@example.com", stderr: "" };
        }
        if (command.endsWith("cursor-agent")) return { exitCode: 0, stdout: "2026.07.23", stderr: "" };
        return { exitCode: 1, stdout: "", stderr: "not present" };
      },
      httpGetJson: async () => ({ ok: false, status: 0 }),
      env: { HOME: "/nonexistent-home-for-this-test" },
    },
  });
  const cursorItem = snapshot.service_inventory.find((item) => item.id === "cursor_cli");
  assert.equal(cursorItem?.status, "ready");
  assert.equal(cursorItem?.metadata?.authenticated, true);
});

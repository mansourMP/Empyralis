import test from "node:test";
import assert from "node:assert/strict";
import fs from "fs";
import os from "os";
import path from "path";

import { buildFastPassiveInventorySnapshot, collectPassiveInventorySnapshot } from "../health/service-inventory";

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
});

test("fast passive inventory snapshot avoids service probes for heartbeat liveness", () => {
  const snapshot = buildFastPassiveInventorySnapshot({
    requestedCapabilities: ["shell.execute", "screenshot.capture"],
    localRunnerReady: true,
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

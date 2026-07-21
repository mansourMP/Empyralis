import { spawn, type ChildProcess } from "child_process";
import { promises as fsp } from "fs";
import os from "os";
import path from "path";

import type { GatewayReleaseLayout } from "./gateway-release-layout";

/**
 * Restart-handoff safety, adapted from OpenClaw's own two-tier design
 * (/Users/mansur/openclaw/src — read-only reference, nothing imported):
 *
 *  - Supervised (systemd): OpenClaw's respawnGatewayProcessForUpdate()
 *    (src/infra/process-respawn.ts:118-148) detects a supervisor via env
 *    hints (src/infra/supervisor-markers.ts:53-80) and, when found, just
 *    exits cleanly and lets the supervisor relaunch — see
 *    detectGatewaySupervisor() below, which mirrors that env-hint approach
 *    (INVOCATION_ID/JOURNAL_STREAM/SYSTEMD_EXEC_PID are set by systemd
 *    itself for every unit it execs, not something we invent). This is safe
 *    here for the same structural reason it's safe in OpenClaw: install-
 *    agent-computer.sh's unit has `Restart=always` (install-agent-computer.
 *    sh:379), so ANY clean exit (even code 0) triggers an automatic
 *    relaunch through run-gateway, which re-resolves its entrypoint through
 *    the `current` symlink we just swapped — the new build loads on its own,
 *    no detached helper process required.
 *
 *  - Unsupervised (a plain macOS user-session process, no launchd job
 *    configured for it today): OpenClaw's managed-service handoff
 *    (src/infra/update-managed-service-handoff.ts:29-449) spawns a detached
 *    helper script that (a) waits for the parent PID to exit, (b) only then
 *    starts the new build, and (c) on failure runs a service-recovery
 *    fallback (systemctl/launchctl kickstart) so the box is never left
 *    without a running gateway. spawnGatewayRestartHandoff() below mirrors
 *    that exact three-step shape via HANDOFF_SCRIPT, adapted since there is
 *    no systemd/launchd unit to kick here: instead of a service-manager
 *    recovery command, the handoff script itself rolls the `current`
 *    symlink back to the previous release and re-spawns THAT build directly
 *    — a self-contained recovery that needs no supervisor at all. This is
 *    the piece that makes "no supervisor" safe: the detached handoff script
 *    is, for the duration of this one restart, acting as the supervisor.
 *
 * gateway-self-update-runtime.ts is the caller that decides which of these
 * two paths to take and never exits the live process until either path has
 * been confirmed (supervised: swap done, ready to let the exit happen;
 * unsupervised: the handoff script's PID is confirmed spawned).
 */

const SYSTEMD_HINT_ENV_VARS = ["INVOCATION_ID", "JOURNAL_STREAM", "SYSTEMD_EXEC_PID"] as const;
const LAUNCHD_HINT_ENV_VARS = ["XPC_SERVICE_NAME", "EMPYRALIS_LAUNCHD_LABEL"] as const;

export type GatewaySupervisorMode = "systemd" | "launchd" | "none";

function hasAnyHint(env: NodeJS.ProcessEnv, keys: readonly string[]): boolean {
  return keys.some((key) => String(env[key] ?? "").trim().length > 0);
}

/** Detects whether this process is currently running under a supervisor
 *  that will relaunch it after a clean exit. See module doc comment above
 *  for why env-hint detection (not a config flag) is the right signal —
 *  systemd sets these automatically for every unit it directly execs; we
 *  never have to declare "I am supervised" ourselves. No macOS launchd job
 *  is wired up by any installer in this repo today (confirmed: no .plist,
 *  no launchd label anywhere in scripts/), so the launchd branch exists as
 *  a forward-compatible hook but will not match on a real box yet — that is
 *  intentional, not a placeholder bug: it means self-update on today's
 *  macOS gateways always takes the unsupervised path below, which is the
 *  honest behavior for a box nothing currently supervises. */
export function detectGatewaySupervisor(
  env: NodeJS.ProcessEnv = process.env,
  platform: NodeJS.Platform = process.platform,
): GatewaySupervisorMode {
  if (platform === "linux" && hasAnyHint(env, SYSTEMD_HINT_ENV_VARS)) {
    return "systemd";
  }
  if (platform === "darwin" && hasAnyHint(env, LAUNCHD_HINT_ENV_VARS)) {
    return "launchd";
  }
  return "none";
}

// Plain CommonJS on purpose: this runs as a standalone `node <script> <params>`
// process, written to a temp file, so it has no dependency on this package's
// compiled dist/ layout still existing (the whole point of the handoff is to
// survive the parent process — and the parent's own dist/ dir — going away).
const HANDOFF_SCRIPT = String.raw`
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const params = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));

function appendLog(line) {
  try {
    fs.mkdirSync(path.dirname(params.logPath), { recursive: true, mode: 0o700 });
    fs.appendFileSync(params.logPath, "[" + new Date().toISOString() + "] " + line + "\n", { mode: 0o600 });
  } catch {
    // Best effort only — a logging failure must never block the handoff itself.
  }
}

function isPidAlive(pid) {
  if (!pid || typeof pid !== "number") return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return Boolean(err && err.code === "EPERM");
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function swapCurrentSymlinkSync(target) {
  const tmp = params.currentSymlinkPath + ".tmp-rollback-" + Math.random().toString(16).slice(2);
  fs.symlinkSync(target, tmp, "dir");
  fs.renameSync(tmp, params.currentSymlinkPath);
}

function spawnGateway(entry) {
  const child = spawn(params.execPath, [entry], {
    cwd: path.dirname(path.dirname(entry)),
    env: process.env,
    detached: true,
    stdio: "ignore",
  });
  child.unref();
  return child;
}

(async () => {
  appendLog("handoff started; waiting for parent pid " + params.parentPid + " to exit (timeout " + params.parentExitTimeoutMs + "ms)");
  const deadline = Date.now() + params.parentExitTimeoutMs;
  while (isPidAlive(params.parentPid) && Date.now() < deadline) {
    await sleep(200);
  }
  if (isPidAlive(params.parentPid)) {
    appendLog("parent pid " + params.parentPid + " did not exit before the handoff timeout; aborting handoff. The old gateway process is presumably still running and serving.");
    return;
  }

  appendLog("parent exited; launching new build: " + params.newEntry);
  let child;
  try {
    child = spawnGateway(params.newEntry);
  } catch (err) {
    appendLog("failed to spawn new build: " + (err && err.stack ? err.stack : String(err)));
    child = null;
  }
  appendLog("new gateway spawn pid=" + (child && child.pid ? child.pid : "none"));

  await sleep(params.healthGraceMs);
  if (child && isPidAlive(child.pid)) {
    appendLog("new gateway still alive after " + params.healthGraceMs + "ms grace period; handoff succeeded.");
    return;
  }

  appendLog("new gateway did not stay alive through the grace period; rolling back.");
  if (!params.previousCurrentTarget || !params.previousEntry) {
    appendLog("no previous release recorded to roll back to — manual recovery is required on this box (install root: " + params.installRoot + ").");
    return;
  }
  try {
    swapCurrentSymlinkSync(params.previousCurrentTarget);
    appendLog("rolled back current symlink to " + params.previousCurrentTarget);
  } catch (err) {
    appendLog("rollback symlink swap failed: " + (err && err.stack ? err.stack : String(err)));
  }
  try {
    const fallback = spawnGateway(params.previousEntry);
    appendLog("relaunched previous build pid=" + (fallback.pid || "unknown"));
  } catch (err) {
    appendLog("failed to relaunch previous build: " + (err && err.stack ? err.stack : String(err)));
  }
})().catch((err) => {
  appendLog("handoff script crashed: " + (err && err.stack ? err.stack : String(err)));
});
`;

export interface SpawnGatewayRestartHandoffOptions {
  layout: GatewayReleaseLayout;
  parentPid: number;
  newEntry: string;
  previousEntry: string | null;
  previousCurrentTarget: string | null;
  /** Where the handoff script's own progress log lands — surfaced back to
   *  the capability-invoke result so a failed handoff is diagnosable without
   *  SSH (the caller reads this file back out over the same tool-invoke
   *  channel on a later recheck, mirroring how imsg install surfaces its
   *  own log). */
  logPath: string;
  execPath?: string;
  parentExitTimeoutMs?: number;
  healthGraceMs?: number;
  /** Injectable for tests — defaults to node:child_process's real spawn.
   *  Tests MUST supply a fake here; the real one launches a genuine
   *  detached process and must never run during `node --test`. */
  spawnImpl?: typeof spawn;
  mkdtempImpl?: (prefix: string) => Promise<string>;
  writeFileImpl?: (filePath: string, contents: string) => Promise<void>;
}

export interface GatewayRestartHandoffSpawnResult {
  pid: number | undefined;
  scriptPath: string;
  paramsPath: string;
  logPath: string;
}

const DEFAULT_PARENT_EXIT_TIMEOUT_MS = 30_000;
const DEFAULT_HEALTH_GRACE_MS = 8_000;

/** Writes the handoff script + its params to a temp dir and spawns it fully
 *  detached (survives this process exiting). Returns as soon as the spawn
 *  call itself succeeds (a `pid` is present) — the caller's contract is
 *  "never exit before the handoff process is confirmed spawned", which this
 *  satisfies by construction: a rejected/thrown spawn propagates to the
 *  caller instead of being swallowed, so the caller can roll back and keep
 *  running instead of exiting into a void. */
export async function spawnGatewayRestartHandoff(
  opts: SpawnGatewayRestartHandoffOptions,
): Promise<GatewayRestartHandoffSpawnResult> {
  const mkdtemp = opts.mkdtempImpl ?? ((prefix: string) => fsp.mkdtemp(prefix));
  const dir = await mkdtemp(path.join(os.tmpdir(), "empyralis-gateway-update-handoff-"));
  const scriptPath = path.join(dir, "handoff.cjs");
  const paramsPath = path.join(dir, "handoff.json");

  const params = {
    parentPid: opts.parentPid,
    newEntry: opts.newEntry,
    previousEntry: opts.previousEntry,
    previousCurrentTarget: opts.previousCurrentTarget,
    currentSymlinkPath: opts.layout.currentSymlinkPath,
    installRoot: opts.layout.installRoot,
    execPath: opts.execPath ?? process.execPath,
    parentExitTimeoutMs: opts.parentExitTimeoutMs ?? DEFAULT_PARENT_EXIT_TIMEOUT_MS,
    healthGraceMs: opts.healthGraceMs ?? DEFAULT_HEALTH_GRACE_MS,
    logPath: opts.logPath,
  };

  const writeFile =
    opts.writeFileImpl ??
    (async (filePath: string, contents: string) => {
      await fsp.writeFile(filePath, contents, { mode: 0o700 });
    });
  await writeFile(scriptPath, `${HANDOFF_SCRIPT}\n`);
  await writeFile(paramsPath, `${JSON.stringify(params, null, 2)}\n`);

  const spawnFn = opts.spawnImpl ?? spawn;
  const child: ChildProcess = spawnFn(params.execPath, [scriptPath, paramsPath], {
    detached: true,
    stdio: "ignore",
  });
  // A synchronous spawn error (e.g. ENOENT on the node binary) surfaces as an
  // "error" event, not a thrown exception, from child_process.spawn — without
  // this listener, an unhandled 'error' event would crash the (still-live,
  // still-serving) parent gateway process. Swallow it here; the caller checks
  // `child.pid` for "did the spawn actually happen" instead.
  child.once("error", () => undefined);
  child.unref?.();

  return { pid: child.pid, scriptPath, paramsPath, logPath: opts.logPath };
}

import fs from "fs";
import os from "os";
import path from "path";

import type { GatewayNativeRuntimeMetadata } from "../runtime/runtime-metadata";
import {
  agentComputerDesktopSession,
  agentComputerSystemServiceModeEnabled,
} from "../runtime/service-mode";
import {
  capabilityPermissionReady,
  capabilityPermissionStatus,
  desktopPermissionForCapability,
  setLlmRuntimeClaudeCodeReady,
  setLlmRuntimeCodexReady,
  setLlmRuntimeCursorReady,
  setLlmRuntimeGrokBuildReady,
  setLlmRuntimeOllamaReady,
  setShellSandboxDockerReady,
  type CapabilityPermissionStatus,
} from "../runtime/desktop-permissions";
import { execFileWithTimeout } from "../shell/exec-file-with-timeout";
import { resolveCommandPath } from "../shell/user-install-dirs";
import { checkOpenClawVersion, OPENCLAW_PINNED_VERSION } from "../openclaw/provisioning/openclaw-version";

export type PassiveServiceStatus = "ready" | "degraded" | "offline" | "missing" | "unknown" | "blocked";

export interface PassiveServiceInventoryItem {
  id: string;
  label: string;
  kind: string;
  status: PassiveServiceStatus;
  detected: boolean;
  passive: true;
  execution_enabled: false;
  check: string;
  summary: string;
  last_checked_at: string;
  metadata?: Record<string, unknown>;
}

export interface PassiveInventorySnapshot {
  service_inventory: PassiveServiceInventoryItem[];
  native_runtime: GatewayNativeRuntimeMetadata;
  capability_readiness: {
    requested: string[];
    ready: string[];
    blocked: string[];
    permission_states: Record<string, CapabilityPermissionStatus>;
    passive_services: string[];
    service_statuses: Record<string, PassiveServiceStatus>;
    // Hardware-readiness gap: the control plane has only ever known what
    // runtime_access_mode IT authorized for this gateway (server-side
    // metadata, set at pairing time) — never what the box operator's own
    // EMPYRALIS_GATEWAY_SHELL_FULL_ACCESS_ENABLED flag (config.ts's
    // shellFullAccessLocallyEnabled, threaded into GatewayShellRuntime in
    // index.ts) is ACTUALLY set to right now on this box. A customer could
    // believe full_access is live when the local half was never enabled, or
    // vice versa, with no way to tell short of a failed shell.execute call.
    // Reported here, alongside the rest of this heartbeat-driven snapshot,
    // so the Settings > Hardware view can show the honest authorized-vs-
    // locally-enabled distinction instead of only the server's half of it.
    shell_full_access_locally_enabled: boolean;
  };
}

interface CommandResult {
  exitCode: number | null;
  stdout: string;
  stderr: string;
  signal?: NodeJS.Signals | null;
  timedOut?: boolean;
}

interface HttpProbeResult {
  ok: boolean;
  status: number;
  body?: unknown;
  error?: string;
}

export interface PassiveInventoryCollectorDeps {
  env?: NodeJS.ProcessEnv;
  platform?: NodeJS.Platform;
  arch?: string;
  release?: string;
  hostname?: string;
  now?: () => Date;
  commandExists?: (command: string) => string | null;
  runCommand?: (command: string, args: string[], timeoutMs: number) => Promise<CommandResult>;
  httpGetJson?: (url: string, timeoutMs: number) => Promise<HttpProbeResult>;
}

export interface PassiveInventoryCollectorOptions {
  requestedCapabilities?: string[];
  localRunnerReady?: boolean;
  /** The box operator's own full_access opt-in (config.ts's
   *  shellFullAccessLocallyEnabled) — a per-process constant read once from
   *  EMPYRALIS_GATEWAY_SHELL_FULL_ACCESS_ENABLED at gateway boot, threaded in
   *  by the caller (cloud/ws-client.ts) rather than probed here. Never part
   *  of collectPassiveInventorySnapshot()'s cache key: unlike Docker/Ollama/
   *  the CLIs, this can't change mid-process, so it's safe to copy straight
   *  onto every snapshot, cached or fresh, without affecting cache validity. */
  shellFullAccessLocallyEnabled?: boolean;
  /** The channel transport's own `--profile <name>` (config.ts's
   *  openclawProfile — every box defaults to "empyralis", never blank in
   *  production). Passed through rather than probed here because
   *  openclaw-cli.ts's own contract is that a blank profile throws (an
   *  unprofiled invocation would target the OPERATOR's real `~/.openclaw`
   *  instance, not the customer's isolated one) — so an empty/omitted value
   *  means "do not probe OpenClaw at all" rather than "probe with no
   *  profile". Never part of the cache key, for the same reason
   *  shellFullAccessLocallyEnabled isn't: a per-process config value that
   *  cannot change mid-process. */
  openclawProfile?: string;
  /** config.ts's openclawBinaryPath (EMPYRALIS_OPENCLAW_BINARY) — checked
   *  before the bare "openclaw" PATH lookup, same precedence every other CLI
   *  probe in this file gives its own *_CLI_PATH override. */
  openclawBinaryPath?: string;
  deps?: PassiveInventoryCollectorDeps;
}

const DEFAULT_COMMAND_TIMEOUT_MS = 1_500;
// `docker info` talks to a daemon inside a VM on macOS/Windows, so it is far
// slower than the CLI-presence checks this file's other probes make — and
// every probe in collectPassiveServiceInventory() is fired concurrently, so
// it pays that cost under contention. Measured on a MacBook Air with Docker
// Desktop running: ~0.5s idle, but 1.9s-4.5s across a 7-way parallel burst
// matching the real probe fan-out. At DEFAULT_COMMAND_TIMEOUT_MS it
// therefore timed out EVERY time, and because this probe's result is what
// feeds setShellSandboxDockerReady() below, a healthy Docker read as
// "offline" and the gateway silently stopped advertising shell.execute and
// filesystem.read_write. The owner saw only "Agent Computer is not
// connected" and had no way to reach the real cause. A capability gate is
// not latency-sensitive; being slow here is fine, being wrong is not.
const DOCKER_COMMAND_TIMEOUT_MS = 10_000;
// openclaw --version is a cold-started Node CLI, same family as the
// codex/claude/grok/cursor probes below, but on a MISSING install this is the
// probe that has to distinguish "not on PATH" (instant) from "on PATH but the
// transport's own bundled Node is slow to boot" (a few seconds) — a shorter
// budget here would misreport a slow-but-real boot as "unreadable version",
// which openclaw-version.ts's checkOpenClawVersion() treats as a genuine
// installed-but-degraded state rather than "probe inconclusive". Bounded
// like every timeout in this file, and off the heartbeat's own critical path
// (see refreshPassiveInventorySnapshot in cloud/ws-client.ts) so this never
// slows an actual heartbeat send.
const OPENCLAW_VERSION_TIMEOUT_MS = 6_000;
// `channels list --all --json` alone (no `config get`) — the same read
// openclaw-channel-setup.ts's readChannelList() makes, at a smaller budget
// than that capability's own 30s: this passive probe only needs the
// installed-plugin COUNT, never blocks a customer action, and is one of
// several probes firing concurrently every cache cycle.
const OPENCLAW_CHANNELS_LIST_TIMEOUT_MS = 15_000;
const PASSIVE_INVENTORY_CACHE_TTL_MS = 60_000;
const OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags";
const MACOS_SYSTEM_PROFILER = "/usr/sbin/system_profiler";
// Desktop-control capabilities that still depend on the old local
// supervisor/runner concept — checkLocalRunnerHealth() in cloud/ws-client.ts
// hardcodes false for these (desktop control is still out of scope; the Rust
// supervisor daemon that implemented it is archived, not rebuilt).
//
// shell.execute and filesystem.read_write used to be in this set too, back
// when they were also dispatched through the same local-runner/supervisor
// path. They are NOT anymore: they now have their own independent,
// Docker-sandboxed executor (GatewayShellRuntime) gated on the "shell_sandbox"
// desktop permission (see runtime/desktop-permissions.ts), which has nothing
// to do with local-runner health. Leaving them in this set would force-block
// them forever regardless of Docker state, since localRunnerReady is always
// false — that would silently defeat the whole shell-sandbox feature.
const LOCAL_RUNNER_CAPABILITIES: ReadonlySet<string> = new Set([
  "screenshot.capture",
  "computer_control.ocr",
  "computer_control.move",
  "computer_control.click",
  "computer_control.type",
  "computer_control.key",
  "computer_control.clipboard_read",
  "computer_control.clipboard_write",
  "computer_control.list_windows",
  "computer_control.list_apps",
  "computer_control.launch",
  "computer_control.launch_app",
  "computer_control.notify",
  "computer_control.applescript",
  "computer_control.speak",
]);

let passiveInventoryCache: { key: string; capturedAtMs: number; snapshot: PassiveInventorySnapshot } | null = null;

/** Force the next collectPassiveInventorySnapshot() call to actually probe
 *  rather than serve up-to-60-second-stale cached data. Called by
 *  cli-setup-runtime after a successful cli.install or cli.login.finalize
 *  so the UI's "Sign in" row flips from "Installed, not signed in" to
 *  "Ready" on the very next heartbeat instead of waiting out the cache
 *  TTL. Never call from a hot code path; there's an intentional single-
 *  flight guard on the async probe itself in ws-client.ts. */
export function invalidatePassiveInventoryCache(): void {
  passiveInventoryCache = null;
}

function truncate(value: unknown, maxLength = 240): string {
  const token = String(value ?? "").replace(/\s+/g, " ").trim();
  return token.length > maxLength ? `${token.slice(0, maxLength - 3)}...` : token;
}

function buildNativeRuntimeSnapshot(deps: PassiveInventoryCollectorDeps = {}): GatewayNativeRuntimeMetadata {
  const env = deps.env ?? process.env;
  return {
    os: deps.platform ?? process.platform,
    arch: deps.arch ?? process.arch,
    release: deps.release ?? os.release(),
    hostname: deps.hostname ?? os.hostname(),
    desktop_session: agentComputerDesktopSession(env),
    system_service_mode: agentComputerSystemServiceModeEnabled(env),
  };
}

// Detection previously only ever scanned the Gateway's own process PATH
// (whatever launchd/systemd/the parent process handed it) — frequently
// narrower than an interactive login shell's, which is the literal reason
// `claude`/`codex` could report "Not installed" on a box where `which
// claude` in a Terminal finds it just fine (confirmed real case: Claude
// Code's native installer puts the binary at `~/.local/bin/claude`, on a
// login shell's PATH via .zshrc/.bashrc/.profile but NOT on the Gateway's).
// resolveCommandPath (shared with llm/cli-login-session.ts's sign-in spawn
// and llm/cli-installer.ts's install verification — see its own doc
// comment) now also checks the standard user-level CLI install locations on
// macOS + Linux, in ADDITION to (never instead of) PATH.
function defaultCommandExists(command: string, env: NodeJS.ProcessEnv, platform: NodeJS.Platform): string | null {
  return resolveCommandPath(command, env, platform);
}

// Every probe below goes through execFileWithTimeout, NOT execFile's own
// `timeout` option. See shell/exec-file-with-timeout.ts: that option sends one
// SIGTERM and never escalates, and its callback still only fires on the child's
// exit — so a `docker info` waiting on a wedged Docker Desktop socket (which
// demonstrably ignores SIGTERM on macOS) left this promise pending forever,
// hung the whole Promise.all in collectPassiveInventorySnapshot, and stranded
// an immortal child process on every probe.
async function defaultRunCommand(command: string, args: string[], timeoutMs: number): Promise<CommandResult> {
  const result = await execFileWithTimeout(command, args, timeoutMs);
  return {
    exitCode: result.exitCode,
    stdout: result.stdout,
    // Every probe below renders `stderr || stdout || "exited with <code>"` into
    // its owner-facing summary, and a timed-out child has neither — without
    // this the Settings > Hardware row would read "docker info exited with
    // null." instead of naming the actual condition.
    stderr: result.timedOut
      ? `Timed out after ${timeoutMs}ms; the process did not exit on its own and was killed.`
      : result.stderr,
    signal: result.signal,
    timedOut: result.timedOut,
  };
}

async function defaultHttpGetJson(url: string, timeoutMs: number): Promise<HttpProbeResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { method: "GET", signal: controller.signal });
    let body: unknown = undefined;
    try {
      body = await response.json();
    } catch {
      body = undefined;
    }
    return { ok: response.ok, status: response.status, body };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      error: error instanceof Error ? error.message : String(error),
    };
  } finally {
    clearTimeout(timer);
  }
}

function makeItem(
  input: Omit<PassiveServiceInventoryItem, "passive" | "execution_enabled" | "last_checked_at">,
  checkedAt: string,
): PassiveServiceInventoryItem {
  return {
    ...input,
    passive: true,
    execution_enabled: false,
    last_checked_at: checkedAt,
  };
}

function summarizePermissionReadiness(statuses: CapabilityPermissionStatus[]): PassiveServiceStatus {
  const relevant = statuses.filter((status) => status.state !== "not_applicable");
  if (relevant.length === 0) {
    return "unknown";
  }
  if (relevant.every((status) => status.state === "granted")) {
    return "ready";
  }
  if (relevant.some((status) => status.state === "denied" || status.state === "restricted")) {
    return "blocked";
  }
  return "degraded";
}

function buildDesktopPermissionInventoryItem(
  requested: string[],
  checkedAt: string,
  env: NodeJS.ProcessEnv,
): PassiveServiceInventoryItem | null {
  const statuses = requested
    .filter((capability) => desktopPermissionForCapability(capability))
    .map((capability) => capabilityPermissionStatus(capability, env));
  if (!statuses.length) {
    return null;
  }
  const summaryStatus = summarizePermissionReadiness(statuses);
  const blocked = statuses.filter((status) => status.state !== "granted" && status.state !== "not_applicable");
  return makeItem({
    id: "desktop_permissions",
    label: "Desktop permissions",
    kind: "permission_state",
    status: summaryStatus,
    detected: true,
    check: "user-session permission status",
    summary: blocked.length
      ? `${blocked.length} desktop permission gate(s) are not ready.`
      : "Desktop permission gates are ready for requested capabilities.",
    metadata: {
      requested_permissions: [...new Set(statuses.map((status) => status.permission))],
      blocked_capabilities: blocked.map((status) => status.capability_id),
    },
  }, checkedAt);
}

function buildLocalRunnerInventoryItem(ready: boolean, checkedAt: string): PassiveServiceInventoryItem {
  return makeItem({
    id: "local_runner",
    label: "Local runner",
    kind: "agent_computer_runtime",
    status: ready ? "ready" : "offline",
    detected: ready,
    check: "GET /health",
    summary: ready
      ? "Agent Computer local runner is reachable."
      : "Agent Computer local runner is not reachable.",
  }, checkedAt);
}

export function applyLocalRunnerReadiness(
  snapshot: PassiveInventorySnapshot,
  localRunnerReady: boolean,
  checkedAt = new Date().toISOString(),
): PassiveInventorySnapshot {
  const serviceInventory = snapshot.service_inventory.filter((item) => item.id !== "local_runner");
  serviceInventory.unshift(buildLocalRunnerInventoryItem(localRunnerReady, checkedAt));
  const requested = [...snapshot.capability_readiness.requested];
  const ready = requested.filter((capability) => {
    if (LOCAL_RUNNER_CAPABILITIES.has(capability) && !localRunnerReady) {
      return false;
    }
    return snapshot.capability_readiness.ready.includes(capability);
  });
  const readySet = new Set(ready);
  const blocked = requested.filter((capability) => !readySet.has(capability));
  const serviceStatuses = Object.fromEntries(
    serviceInventory.map((item) => [item.id, item.status]),
  ) as Record<string, PassiveServiceStatus>;
  return {
    ...snapshot,
    service_inventory: serviceInventory,
    capability_readiness: {
      ...snapshot.capability_readiness,
      ready,
      blocked,
      passive_services: serviceInventory.map((item) => item.id),
      service_statuses: serviceStatuses,
    },
  };
}

export function buildFastPassiveInventorySnapshot(
  options: PassiveInventoryCollectorOptions = {},
): PassiveInventorySnapshot {
  const deps = options.deps ?? {};
  const env = deps.env ?? process.env;
  const requested = [...(options.requestedCapabilities ?? [])];
  const checkedAt = (deps.now ?? (() => new Date()))().toISOString();
  const nativeRuntime = buildNativeRuntimeSnapshot(deps);
  const serviceInventory: PassiveServiceInventoryItem[] = [];
  if (typeof options.localRunnerReady === "boolean") {
    serviceInventory.push(buildLocalRunnerInventoryItem(options.localRunnerReady, checkedAt));
  }
  const permissionInventory = buildDesktopPermissionInventoryItem(requested, checkedAt, env);
  if (permissionInventory) {
    serviceInventory.push(permissionInventory);
  }
  const serviceStatuses = Object.fromEntries(
    serviceInventory.map((item) => [item.id, item.status]),
  ) as Record<string, PassiveServiceStatus>;
  const permissionStates = Object.fromEntries(
    requested
      .map((capability) => [capability, capabilityPermissionStatus(capability, env)] as const)
      .filter(([, status]) => status.state !== "not_applicable"),
  );
  const snapshot = {
    service_inventory: serviceInventory,
    native_runtime: nativeRuntime,
    capability_readiness: {
      requested,
      ready: requested.filter((capability) => capabilityPermissionReady(capability, env)),
      blocked: requested.filter((capability) => !capabilityPermissionReady(capability, env)),
      permission_states: permissionStates,
      passive_services: serviceInventory.map((item) => item.id),
      service_statuses: serviceStatuses,
      shell_full_access_locally_enabled: Boolean(options.shellFullAccessLocallyEnabled),
    },
  };
  return typeof options.localRunnerReady === "boolean"
    ? applyLocalRunnerReadiness(snapshot, options.localRunnerReady, checkedAt)
    : snapshot;
}

async function probePostgres(
  checkedAt: string,
  commandExists: (command: string) => string | null,
  runCommand: (command: string, args: string[], timeoutMs: number) => Promise<CommandResult>,
): Promise<PassiveServiceInventoryItem> {
  const command = commandExists("pg_isready");
  if (!command) {
    return makeItem({
      id: "postgres",
      label: "Postgres",
      kind: "database",
      status: "missing",
      detected: false,
      check: "pg_isready",
      summary: "pg_isready is not installed on this target.",
    }, checkedAt);
  }
  const result = await runCommand(command, ["-q"], DEFAULT_COMMAND_TIMEOUT_MS);
  const ready = result.exitCode === 0;
  return makeItem({
    id: "postgres",
    label: "Postgres",
    kind: "database",
    status: ready ? "ready" : "offline",
    detected: true,
    check: "pg_isready -q",
    summary: ready
      ? "Postgres accepts local readiness checks."
      : truncate(result.stderr || result.stdout || `pg_isready exited with ${result.exitCode}.`),
    metadata: { exit_code: result.exitCode, timed_out: Boolean(result.timedOut) },
  }, checkedAt);
}

async function probeDocker(
  checkedAt: string,
  commandExists: (command: string) => string | null,
  runCommand: (command: string, args: string[], timeoutMs: number) => Promise<CommandResult>,
): Promise<PassiveServiceInventoryItem> {
  const command = commandExists("docker");
  if (!command) {
    return makeItem({
      id: "docker",
      label: "Docker",
      kind: "container_runtime",
      status: "missing",
      detected: false,
      check: "docker info",
      summary: "Docker CLI is not installed on this target.",
    }, checkedAt);
  }
  const result = await runCommand(command, ["info", "--format", "{{.ServerVersion}}"], DOCKER_COMMAND_TIMEOUT_MS);
  const ready = result.exitCode === 0;
  return makeItem({
    id: "docker",
    label: "Docker",
    kind: "container_runtime",
    status: ready ? "ready" : "offline",
    detected: true,
    check: "docker info --format {{.ServerVersion}}",
    summary: ready
      ? `Docker daemon is available${truncate(result.stdout, 80) ? ` (${truncate(result.stdout, 80)})` : ""}.`
      : truncate(result.stderr || result.stdout || `docker info exited with ${result.exitCode}.`),
    metadata: { exit_code: result.exitCode, timed_out: Boolean(result.timedOut) },
  }, checkedAt);
}

async function probeOllama(
  checkedAt: string,
  httpGetJson: (url: string, timeoutMs: number) => Promise<HttpProbeResult>,
): Promise<PassiveServiceInventoryItem> {
  const result = await httpGetJson(OLLAMA_TAGS_URL, DEFAULT_COMMAND_TIMEOUT_MS);
  const modelCount = Array.isArray((result.body as { models?: unknown } | undefined)?.models)
    ? ((result.body as { models: unknown[] }).models).length
    : undefined;
  return makeItem({
    id: "ollama",
    label: "Ollama",
    kind: "local_model_runtime",
    status: result.ok ? "ready" : "offline",
    detected: result.ok,
    check: "GET /api/tags",
    summary: result.ok
      ? `Ollama API is reachable${typeof modelCount === "number" ? ` with ${modelCount} model(s)` : ""}.`
      : truncate(result.error || `Ollama API returned status ${result.status}.`),
    // Ollama is a LOCAL runtime with no login: "authenticated" tracks
    // reachability so the box-picker can show it uniformly with the CLIs.
    metadata: {
      status_code: result.status,
      model_count: modelCount,
      installed: result.ok,
      authenticated: result.ok,
      has_models: typeof modelCount === "number" ? modelCount > 0 : false,
    },
  }, checkedAt);
}

// Read-only auth-presence check for an installed AI CLI. We check ONLY whether
// an auth MARKER exists — a credential file on disk (existence only, via
// fs.existsSync) or an env var being non-empty. We NEVER open, read, or
// transmit the CONTENTS of any credential file. This yields the
// installed-vs-authenticated distinction with zero credential exposure.
function detectAuthPresence(
  filePaths: string[],
  envKeys: string[],
  env: NodeJS.ProcessEnv,
): boolean {
  for (const key of envKeys) {
    if (String(env[key] ?? "").trim()) {
      return true;
    }
  }
  for (const filePath of filePaths) {
    if (!filePath) {
      continue;
    }
    try {
      if (fs.existsSync(filePath)) {
        return true;
      }
    } catch {
      // Inaccessible path — treat as absent, never surface the error.
    }
  }
  return false;
}

function homeDir(env: NodeJS.ProcessEnv): string {
  return String(env.HOME || env.USERPROFILE || "").trim() || os.homedir();
}

async function probeCodexCli(
  checkedAt: string,
  env: NodeJS.ProcessEnv,
  commandExists: (command: string) => string | null,
  runCommand: (command: string, args: string[], timeoutMs: number) => Promise<CommandResult>,
): Promise<PassiveServiceInventoryItem> {
  const candidates = [
    String(env.CODEX_CLI_PATH || "").trim(),
    "codex",
    "/Applications/Codex.app/Contents/Resources/codex",
  ].filter(Boolean);
  const command = candidates.map(commandExists).find(Boolean) || null;
  if (!command) {
    return makeItem({
      id: "codex_cli",
      label: "Codex CLI",
      kind: "developer_tool",
      status: "missing",
      detected: false,
      check: "codex --version",
      summary: "Codex CLI was not found on PATH or in the Codex app bundle.",
      metadata: { installed: false, authenticated: false },
    }, checkedAt);
  }
  const result = await runCommand(command, ["--version"], DEFAULT_COMMAND_TIMEOUT_MS);
  // The binary was found on PATH above, so it IS installed. `--version` is only
  // a best-effort version/liveness string — a slow cold start (these are Node
  // CLIs) must never flip installed → false and produce a flaky signal.
  const installed = true;
  const authenticated = detectAuthPresence(
    [path.join(homeDir(env), ".codex", "auth.json")],
    ["CODEX_API_KEY"],
    env,
  );
  return makeItem({
    id: "codex_cli",
    label: "Codex CLI",
    kind: "developer_tool",
    status: installed ? (authenticated ? "ready" : "degraded") : "degraded",
    detected: true,
    check: "codex --version",
    summary: installed
      ? (authenticated
          ? truncate(result.stdout || "Codex CLI is installed and signed in.")
          : "Codex CLI is installed but not signed in (run `codex login`).")
      : truncate(result.stderr || result.stdout || `codex --version exited with ${result.exitCode}.`),
    metadata: { path: command, exit_code: result.exitCode, installed, authenticated, timed_out: Boolean(result.timedOut) },
  }, checkedAt);
}

// Claude Code's own credential-storage locations, per
// https://code.claude.com/docs/en/authentication#credential-management:
// macOS always uses the login Keychain (never a file, regardless of
// CLAUDE_CONFIG_DIR); Linux and Windows write ~/.claude/.credentials.json,
// relocated under CLAUDE_CONFIG_DIR when that env var is set. This mirrors
// that precisely so the passive probe never disagrees with the real CLI
// about where its own credential lives.
function claudeCredentialFileCandidates(env: NodeJS.ProcessEnv, platform: NodeJS.Platform): string[] {
  const home = homeDir(env);
  const candidates = [
    path.join(home, ".claude", ".credentials.json"),
    path.join(home, ".claude", "credentials.json"),
  ];
  if (platform !== "darwin") {
    const configDir = String(env.CLAUDE_CONFIG_DIR || "").trim();
    if (configDir) {
      candidates.unshift(path.join(configDir, ".credentials.json"));
    }
  }
  return candidates;
}

async function probeClaudeCli(
  checkedAt: string,
  env: NodeJS.ProcessEnv,
  platform: NodeJS.Platform,
  commandExists: (command: string) => string | null,
  runCommand: (command: string, args: string[], timeoutMs: number) => Promise<CommandResult>,
): Promise<PassiveServiceInventoryItem> {
  const candidates = [
    String(env.CLAUDE_CLI_PATH || "").trim(),
    "claude",
  ].filter(Boolean);
  const command = candidates.map(commandExists).find(Boolean) || null;
  if (!command) {
    return makeItem({
      id: "claude_cli",
      label: "Claude Code CLI",
      kind: "developer_tool",
      status: "missing",
      detected: false,
      check: "claude --version",
      summary: "Claude Code CLI was not found on PATH.",
      metadata: { installed: false, authenticated: false },
    }, checkedAt);
  }
  const result = await runCommand(command, ["--version"], DEFAULT_COMMAND_TIMEOUT_MS);
  // The binary was found on PATH above, so it IS installed. `--version` is only
  // a best-effort version/liveness string — a slow cold start (these are Node
  // CLIs) must never flip installed → false and produce a flaky signal.
  const installed = true;
  // CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY are the mechanism Anthropic
  // itself documents for headless/CI use (a long-lived token from `claude
  // setup-token`, meant to be exported wherever Claude Code runs
  // non-interactively) — see
  // https://code.claude.com/docs/en/authentication#generate-a-long-lived-token.
  // Both sit ABOVE plain /login credentials in the CLI's own auth precedence,
  // so detecting either here is a fully deterministic, OS-independent signal:
  // no Keychain/session dependency, unlike the macOS branch below.
  let authenticated = detectAuthPresence(
    claudeCredentialFileCandidates(env, platform),
    ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"],
    env,
  );
  if (!authenticated && platform === "darwin") {
    // On macOS, Claude Code stores its OAuth token in the login Keychain, not a
    // file. `security find-generic-password` WITHOUT -w returns only the item's
    // metadata + a 0 exit status when it EXISTS — it never emits the secret.
    // This keeps the "never read credential contents" rule while making the
    // installed-vs-authenticated signal accurate on macOS boxes too.
    //
    // Caveat this branch cannot resolve: this is an EXISTENCE check, not proof
    // that a later, separately-spawned, non-interactive child process (e.g.
    // this same Gateway spawning `claude -p ...` for a real agent turn) can
    // actually decrypt the item — Keychain access-control lists are enforced
    // per requesting application at USE time, not at existence-query time, and
    // are known to behave differently for headless/background process
    // contexts than for an interactive Terminal session. Treat a "ready" that
    // came from this branch alone as a reasonable but unverified signal; if
    // dispatch keeps failing "not authenticated" despite this reporting ready,
    // the reliable fix is CLAUDE_CODE_OAUTH_TOKEN above, not re-probing the
    // Keychain harder.
    const security = commandExists("security");
    if (security) {
      try {
        const kc = await runCommand(
          security,
          ["find-generic-password", "-s", "Claude Code-credentials"],
          DEFAULT_COMMAND_TIMEOUT_MS,
        );
        if (kc.exitCode === 0) {
          authenticated = true;
        }
      } catch {
        // Keychain unavailable/locked — leave the conservative file-based result.
      }
    }
  }
  return makeItem({
    id: "claude_cli",
    label: "Claude Code CLI",
    kind: "developer_tool",
    status: installed ? (authenticated ? "ready" : "degraded") : "degraded",
    detected: true,
    check: "claude --version",
    summary: installed
      ? (authenticated
          ? truncate(result.stdout || "Claude Code CLI is installed and signed in.")
          : "Claude Code CLI is installed but not signed in (run `claude login`).")
      : truncate(result.stderr || result.stdout || `claude --version exited with ${result.exitCode}.`),
    metadata: { path: command, exit_code: result.exitCode, installed, authenticated, timed_out: Boolean(result.timedOut) },
  }, checkedAt);
}

// Grok Build's own credential-storage location, per docs.x.ai/build's
// authentication guide (crates/codegen/xai-grok-pager/docs/user-guide/
// 02-authentication.md, fetched live 2026-07-24): "Grok stores credentials
// in ~/.grok/auth.json and reuses them across sessions." Not overridable by
// an env var the way Claude's CLAUDE_CONFIG_DIR is (no such var is
// documented for Grok), so there is exactly one path to check.
function grokAuthFileCandidate(env: NodeJS.ProcessEnv): string {
  return path.join(homeDir(env), ".grok", "auth.json");
}

async function probeGrokBuildCli(
  checkedAt: string,
  env: NodeJS.ProcessEnv,
  commandExists: (command: string) => string | null,
  runCommand: (command: string, args: string[], timeoutMs: number) => Promise<CommandResult>,
): Promise<PassiveServiceInventoryItem> {
  const candidates = [String(env.GROK_CLI_PATH || "").trim(), "grok"].filter(Boolean);
  const command = candidates.map(commandExists).find(Boolean) || null;
  if (!command) {
    return makeItem({
      id: "grok_cli",
      label: "Grok Build CLI",
      kind: "developer_tool",
      status: "missing",
      detected: false,
      check: "grok --version",
      summary: "Grok Build CLI was not found on PATH.",
      metadata: { installed: false, authenticated: false },
    }, checkedAt);
  }
  const result = await runCommand(command, ["--version"], DEFAULT_COMMAND_TIMEOUT_MS);
  const installed = true;
  // XAI_API_KEY is Grok's own documented env-var fallback, always usable
  // headlessly with zero login-session dependency — checked first, same
  // precedence Grok's own auth-resolution order gives it relative to a
  // stored session token being ABSENT. auth.json existence is the signal for
  // a completed `grok login`/`grok login --device-auth` session (background-
  // refreshed by the CLI itself; see cli-login-session.ts's grok_build
  // comment for what happens when that refresh fails).
  const authenticated = detectAuthPresence([grokAuthFileCandidate(env)], ["XAI_API_KEY"], env);
  return makeItem({
    id: "grok_cli",
    label: "Grok Build CLI",
    kind: "developer_tool",
    status: installed ? (authenticated ? "ready" : "degraded") : "degraded",
    detected: true,
    check: "grok --version",
    summary: installed
      ? (authenticated
          ? truncate(result.stdout || "Grok Build CLI is installed and signed in.")
          : "Grok Build CLI is installed but not signed in (run `grok login --device-auth`).")
      : truncate(result.stderr || result.stdout || `grok --version exited with ${result.exitCode}.`),
    metadata: { path: command, exit_code: result.exitCode, installed, authenticated, timed_out: Boolean(result.timedOut) },
  }, checkedAt);
}

// Cursor CLI's authenticated-status check, UNLIKE the other three CLIs
// above, is deliberately NOT a credential-file existence check. Cursor's own
// docs (cursor.com/docs/cli/reference/authentication, fetched live
// 2026-07-24) confirm credentials are "stored locally" and checkable via
// `agent status` / `agent status --format json`, but do NOT publish the
// exact file path or on-disk shape the way Claude Code (Keychain / ~/.claude/
// .credentials.json), Codex (~/.codex/auth.json), and Grok Build (~/.grok/
// auth.json) all do. Guessing an unpublished path risks the exact silent
// failure this rail exists to prevent (a probe that always reads "false" —
// or worse, always "true" — against a path that was never real). So this
// probe instead: (1) trusts CURSOR_API_KEY the same documented way the other
// three trust their own API-key env vars, and (2) otherwise runs `cursor-
// agent status` and looks for its own documented not-authenticated wording,
// never parsing an unconfirmed JSON schema. This is a best-effort UI-hint
// signal, same caveat already established for probeClaudeCli's macOS
// Keychain branch: the REAL, authoritative check is cli-runner.ts's
// parseCursorOutput classifying the actual turn-time failure text (verified
// live: "Error: Authentication required. Please run 'agent login' first, or
// set CURSOR_API_KEY environment variable.") — if this passive probe is ever
// wrong, that turn-time check still fails loudly with a correct "not signed
// in" message; it does not fail silently.
const CURSOR_STATUS_NOT_AUTHENTICATED_MARKERS = [
  "not authenticated", "not logged in", "authentication required", "please run 'agent login'",
  "please run \"agent login\"", "no active session",
];

async function probeCursorCli(
  checkedAt: string,
  env: NodeJS.ProcessEnv,
  commandExists: (command: string) => string | null,
  runCommand: (command: string, args: string[], timeoutMs: number) => Promise<CommandResult>,
): Promise<PassiveServiceInventoryItem> {
  // Cursor's own install script (cursor.com/install, inspected directly
  // 2026-07-24) symlinks BOTH `cursor-agent` (legacy) and `agent` (its new
  // primary name) to the same binary — `cursor-agent` is checked first since
  // it's unambiguous, `agent` is a plausible but genuinely generic PATH name.
  const candidates = [String(env.CURSOR_CLI_PATH || "").trim(), "cursor-agent", "agent"].filter(Boolean);
  const command = candidates.map(commandExists).find(Boolean) || null;
  if (!command) {
    return makeItem({
      id: "cursor_cli",
      label: "Cursor CLI",
      kind: "developer_tool",
      status: "missing",
      detected: false,
      check: "cursor-agent --version",
      summary: "Cursor CLI was not found on PATH.",
      metadata: { installed: false, authenticated: false },
    }, checkedAt);
  }
  const result = await runCommand(command, ["--version"], DEFAULT_COMMAND_TIMEOUT_MS);
  const installed = true;
  let authenticated = String(env.CURSOR_API_KEY ?? "").trim().length > 0;
  if (!authenticated) {
    const statusResult = await runCommand(command, ["status"], DEFAULT_COMMAND_TIMEOUT_MS);
    const statusText = `${statusResult.stdout}\n${statusResult.stderr}`.toLowerCase();
    const looksUnauthenticated = CURSOR_STATUS_NOT_AUTHENTICATED_MARKERS.some((marker) => statusText.includes(marker));
    // exitCode 0 with no unauthenticated marker is the best signal available
    // without an officially-published status schema to parse — see this
    // function's doc comment for why this is a deliberately best-effort UI
    // hint, not the turn-gating source of truth.
    authenticated = statusResult.exitCode === 0 && !looksUnauthenticated;
  }
  return makeItem({
    id: "cursor_cli",
    label: "Cursor CLI",
    kind: "developer_tool",
    status: installed ? (authenticated ? "ready" : "degraded") : "degraded",
    detected: true,
    check: "cursor-agent --version",
    summary: installed
      ? (authenticated
          ? truncate(result.stdout || "Cursor CLI is installed and signed in.")
          : "Cursor CLI is installed but not signed in (run `cursor-agent login`).")
      : truncate(result.stderr || result.stdout || `cursor-agent --version exited with ${result.exitCode}.`),
    metadata: { path: command, exit_code: result.exitCode, installed, authenticated, timed_out: Boolean(result.timedOut) },
  }, checkedAt);
}

function macDisplayNames(payload: string): string[] {
  try {
    const parsed = JSON.parse(payload) as { SPDisplaysDataType?: unknown };
    const displays = Array.isArray(parsed.SPDisplaysDataType) ? parsed.SPDisplaysDataType : [];
    return displays
      .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object"))
      .map((item) => truncate(item.sppci_model || item._name || item.spdisplays_vendor || "", 120))
      .filter(Boolean);
  } catch {
    return [];
  }
}

async function probeGpu(
  checkedAt: string,
  platform: NodeJS.Platform,
  commandExists: (command: string) => string | null,
  runCommand: (command: string, args: string[], timeoutMs: number) => Promise<CommandResult>,
): Promise<PassiveServiceInventoryItem> {
  const nvidiaSmi = commandExists("nvidia-smi");
  if (nvidiaSmi) {
    const result = await runCommand(nvidiaSmi, ["--query-gpu=name", "--format=csv,noheader"], DEFAULT_COMMAND_TIMEOUT_MS);
    const ready = result.exitCode === 0;
    const names = result.stdout.split(/\r?\n/).map((line) => truncate(line, 120)).filter(Boolean);
    return makeItem({
      id: "gpu",
      label: "GPU",
      kind: "accelerator",
      status: ready ? "ready" : "degraded",
      detected: ready,
      check: "nvidia-smi --query-gpu=name",
      summary: ready && names.length ? `GPU detected: ${names.join(", ")}.` : truncate(result.stderr || result.stdout || "GPU probe failed."),
      metadata: { vendor: "nvidia", names, exit_code: result.exitCode, timed_out: Boolean(result.timedOut) },
    }, checkedAt);
  }

  const systemProfiler = platform === "darwin" ? commandExists(MACOS_SYSTEM_PROFILER) : null;
  if (systemProfiler) {
    const result = await runCommand(systemProfiler, ["SPDisplaysDataType", "-json", "-detailLevel", "mini"], 3_000);
    const names = result.exitCode === 0 ? macDisplayNames(result.stdout) : [];
    return makeItem({
      id: "gpu",
      label: "GPU",
      kind: "accelerator",
      status: names.length ? "ready" : "unknown",
      detected: names.length > 0,
      check: "system_profiler SPDisplaysDataType",
      summary: names.length ? `GPU detected: ${names.join(", ")}.` : truncate(result.stderr || result.stdout || "GPU probe did not return display hardware."),
      metadata: { vendor: "apple_or_macos", names, exit_code: result.exitCode, timed_out: Boolean(result.timedOut) },
    }, checkedAt);
  }

  return makeItem({
    id: "gpu",
    label: "GPU",
    kind: "accelerator",
    status: "unknown",
    detected: false,
    check: "nvidia-smi/system_profiler",
    summary: "No passive GPU probe is available for this target.",
  }, checkedAt);
}

// ── OpenClaw / channel transport ─────────────────────────────────────────
//
// Both probes below follow the SAME commandExists/runCommand DI every other
// probe in this file uses, deliberately NOT the OpenClawCli class
// (openclaw/provisioning/openclaw-cli.ts) — that class owns its own exec
// path and does not accept this file's deps.commandExists/deps.runCommand
// seam, so reusing it here would make this file's probes bypass test
// mocking and spawn a real child process during unit tests. checkOpenClawVersion
// / parseOpenClawVersion (openclaw-version.ts) ARE reused: they are pure,
// input -> output functions with no process access, so there is one place
// that decides "does this observed string satisfy the pin", not two.
//
// This is the observed-not-declared half of the box report the incident
// (empyralis.ai/install/agent-computer.sh silently serving a stale,
// zero-Docker, zero-OpenClaw installer to every real customer box) exists
// to close: the cloud never asked the box what it actually ended up with,
// so a broken install and a healthy one looked identical from the product.

interface OpenClawChannelListSummary {
  installedCount: number;
  totalCount: number;
  installedChannelIds: string[];
}

/** Parses `channels list --all --json`'s `{"chat": {"<id>": {"installed":
 *  bool, ...}, ...}}` shape (same shape openclaw-channel-setup.ts's
 *  readChannelList() reads) into an installed/total count. Returns undefined
 *  on anything that isn't that shape — a probe that CANNOT be read is
 *  reported as "we could not ask", never coerced into "zero installed". */
function parseOpenClawChannelListSummary(stdout: string): OpenClawChannelListSummary | undefined {
  let parsed: unknown;
  try {
    parsed = JSON.parse(stdout);
  } catch {
    return undefined;
  }
  const chat = (parsed as { chat?: unknown } | null)?.chat;
  if (!chat || typeof chat !== "object") {
    return undefined;
  }
  const entries = Object.entries(chat as Record<string, unknown>);
  const installedChannelIds = entries
    .filter(([, entry]) => Boolean(entry && typeof entry === "object" && (entry as { installed?: unknown }).installed === true))
    .map(([id]) => id)
    .sort();
  return {
    installedCount: installedChannelIds.length,
    totalCount: entries.length,
    installedChannelIds,
  };
}

/** Resolves the openclaw binary the same way every other CLI probe in this
 *  file resolves its own: an explicit override first (here, config.ts's
 *  openclawBinaryPath, threaded in as options.openclawBinaryPath), then the
 *  bare command on PATH. */
function resolveOpenClawCommand(
  openclawBinaryPath: string | undefined,
  commandExists: (command: string) => string | null,
): string | null {
  const candidates = [String(openclawBinaryPath || "").trim(), "openclaw"].filter(Boolean);
  return candidates.map(commandExists).find(Boolean) || null;
}

/** Installed + version-against-pin, as an observed service_inventory item —
 *  the same passive family as probeDocker/probeClaudeCli above, so it goes
 *  through the identical generic sanitizer/cache/heartbeat plumbing with no
 *  backend change required (gateway_inventory_service.py's
 *  sanitize_service_inventory has no id allowlist).
 *
 *  Undefined `openclawProfile` (no per-process config, or a caller that
 *  never opted in) means "do not probe" rather than "probe with no
 *  profile" — see the option's own doc comment for why an unprofiled
 *  invocation is unsafe, not just untested. */
async function probeOpenClaw(
  checkedAt: string,
  openclawProfile: string | undefined,
  openclawBinaryPath: string | undefined,
  commandExists: (command: string) => string | null,
  runCommand: (command: string, args: string[], timeoutMs: number) => Promise<CommandResult>,
): Promise<PassiveServiceInventoryItem | null> {
  const profile = String(openclawProfile || "").trim();
  if (!profile) {
    return null;
  }
  const command = resolveOpenClawCommand(openclawBinaryPath, commandExists);
  if (!command) {
    const check = checkOpenClawVersion(undefined);
    return makeItem({
      id: "openclaw",
      label: "Channel transport",
      kind: "channel_transport",
      status: "missing",
      detected: false,
      check: "openclaw --version",
      summary: check.detail || "The channel transport is not installed on this computer.",
      metadata: { installed: false, pinned_version: OPENCLAW_PINNED_VERSION, version_match: false, code: check.code },
    }, checkedAt);
  }
  const result = await runCommand(command, ["--profile", profile, "--version"], OPENCLAW_VERSION_TIMEOUT_MS);
  // Genuinely could not ask (the binary exists but the invocation itself
  // failed to complete) — distinct from "asked, and the answer says it is
  // not installed / not the pinned build". A wedged or timed-out probe must
  // never present as "missing": that is the exact "cannot be probed is not
  // the same as absent" collapse this report exists to avoid.
  if (result.timedOut) {
    return makeItem({
      id: "openclaw",
      label: "Channel transport",
      kind: "channel_transport",
      status: "unknown",
      detected: true,
      check: "openclaw --version",
      summary: "Could not ask this computer whether the channel transport is ready in time.",
      metadata: { installed: undefined, pinned_version: OPENCLAW_PINNED_VERSION, version_match: undefined, probe_timed_out: true },
    }, checkedAt);
  }
  const rawOutput = result.exitCode === 0 ? `${result.stdout}\n${result.stderr}`.trim() : undefined;
  const check = checkOpenClawVersion(rawOutput);
  const status: PassiveServiceStatus = check.ok ? "ready" : (check.code === "openclaw_not_installed" ? "missing" : "degraded");
  return makeItem({
    id: "openclaw",
    label: "Channel transport",
    kind: "channel_transport",
    status,
    detected: status !== "missing",
    check: "openclaw --version",
    summary: check.ok
      ? `Channel transport is installed at the required version (${check.observed}).`
      : (check.detail || "Channel transport version could not be confirmed."),
    metadata: {
      installed: status !== "missing",
      observed_version: check.observed,
      pinned_version: OPENCLAW_PINNED_VERSION,
      version_match: check.ok,
      code: check.code,
    },
  }, checkedAt);
}

/** Which channel plugins are actually installed, as its own observed item —
 *  separate from probeOpenClaw() above because "the transport itself is
 *  absent" and "the transport is present with zero channel plugins" are
 *  different facts with different remediation, and folding a count into the
 *  other item's metadata would hide the second one behind the first. Only
 *  meaningful once the transport is confirmed present: an absent transport
 *  makes this a known "missing" fact (there is nothing to enumerate), not an
 *  unknown one. */
async function probeOpenClawChannelPlugins(
  checkedAt: string,
  openclawItem: PassiveServiceInventoryItem | null,
  openclawProfile: string | undefined,
  openclawBinaryPath: string | undefined,
  commandExists: (command: string) => string | null,
  runCommand: (command: string, args: string[], timeoutMs: number) => Promise<CommandResult>,
): Promise<PassiveServiceInventoryItem | null> {
  const profile = String(openclawProfile || "").trim();
  if (!profile || !openclawItem) {
    return null;
  }
  if (openclawItem.status === "missing") {
    return makeItem({
      id: "openclaw_channel_plugins",
      label: "Channel plugins",
      kind: "channel_transport",
      status: "missing",
      detected: false,
      check: "openclaw channels list --all --json",
      summary: "No channel plugins are installed: the channel transport itself is not installed.",
      metadata: { installed_count: 0, total_count: 0 },
    }, checkedAt);
  }
  const command = resolveOpenClawCommand(openclawBinaryPath, commandExists);
  if (!command) {
    // Transport reported ready/degraded a moment ago but the binary is gone
    // now (race, or a deps mock that only wired the version call) — report
    // what we can rather than assume.
    return makeItem({
      id: "openclaw_channel_plugins",
      label: "Channel plugins",
      kind: "channel_transport",
      status: "unknown",
      detected: false,
      check: "openclaw channels list --all --json",
      summary: "Could not ask this computer which channel plugins are installed.",
      metadata: { installed_count: undefined, total_count: undefined },
    }, checkedAt);
  }
  const result = await runCommand(
    command,
    ["--profile", profile, "channels", "list", "--all", "--json"],
    OPENCLAW_CHANNELS_LIST_TIMEOUT_MS,
  );
  const summary = result.exitCode === 0 && !result.timedOut ? parseOpenClawChannelListSummary(result.stdout) : undefined;
  if (!summary) {
    return makeItem({
      id: "openclaw_channel_plugins",
      label: "Channel plugins",
      kind: "channel_transport",
      status: "unknown",
      detected: false,
      check: "openclaw channels list --all --json",
      summary: result.timedOut
        ? "Could not ask this computer which channel plugins are installed in time."
        : truncate(result.stderr || result.stdout || `channels list exited with ${result.exitCode}.`),
      metadata: { installed_count: undefined, total_count: undefined, timed_out: Boolean(result.timedOut) },
    }, checkedAt);
  }
  return makeItem({
    id: "openclaw_channel_plugins",
    label: "Channel plugins",
    kind: "channel_transport",
    status: summary.installedCount > 0 ? "ready" : "degraded",
    detected: summary.installedCount > 0,
    check: "openclaw channels list --all --json",
    summary: summary.installedCount > 0
      ? `${summary.installedCount} of ${summary.totalCount} channel plugin(s) installed.`
      : "The channel transport is installed but no channel plugins are installed yet.",
    metadata: {
      installed_count: summary.installedCount,
      total_count: summary.totalCount,
      installed_channel_ids: summary.installedChannelIds.slice(0, 20),
    },
  }, checkedAt);
}

export async function collectPassiveInventorySnapshot(
  options: PassiveInventoryCollectorOptions = {},
): Promise<PassiveInventorySnapshot> {
  const deps = options.deps ?? {};
  const env = deps.env ?? process.env;
  const platform = deps.platform ?? process.platform;
  const hasCustomDeps = Object.keys(deps).length > 0;
  const requested = [...(options.requestedCapabilities ?? [])];
  const serviceMode = agentComputerSystemServiceModeEnabled(env);
  const permissionKey = requested.map((capability) => capabilityPermissionStatus(capability, env).state);
  const cacheKey = JSON.stringify({ requested, serviceMode, permissionKey });
  if (!hasCustomDeps && passiveInventoryCache?.key === cacheKey) {
    const cacheAgeMs = Date.now() - passiveInventoryCache.capturedAtMs;
    if (cacheAgeMs >= 0 && cacheAgeMs <= PASSIVE_INVENTORY_CACHE_TTL_MS) {
      return passiveInventoryCache.snapshot;
    }
  }
  const checkedAt = (deps.now ?? (() => new Date()))().toISOString();
  const commandExists = deps.commandExists ?? ((command: string) => defaultCommandExists(command, env, platform));
  const runCommand = deps.runCommand ?? defaultRunCommand;
  const httpGetJson = deps.httpGetJson ?? defaultHttpGetJson;
  const nativeRuntime = buildNativeRuntimeSnapshot(deps);

  const [
    postgresItem,
    dockerItemResult,
    ollamaItemResult,
    codexCliItemResult,
    claudeCliItemResult,
    grokCliItemResult,
    cursorCliItemResult,
    gpuItem,
    openclawItem,
  ] = await Promise.all([
    probePostgres(checkedAt, commandExists, runCommand),
    probeDocker(checkedAt, commandExists, runCommand),
    probeOllama(checkedAt, httpGetJson),
    probeCodexCli(checkedAt, env, commandExists, runCommand),
    probeClaudeCli(checkedAt, env, platform, commandExists, runCommand),
    probeGrokBuildCli(checkedAt, env, commandExists, runCommand),
    probeCursorCli(checkedAt, env, commandExists, runCommand),
    probeGpu(checkedAt, platform, commandExists, runCommand),
    probeOpenClaw(checkedAt, options.openclawProfile, options.openclawBinaryPath, commandExists, runCommand),
  ]);
  // Depends on openclawItem's just-computed result (absent transport short-
  // circuits to a known "missing" fact rather than a second probe), so it
  // cannot join the Promise.all above — still concurrent with nothing else,
  // but that's fine: it's off the heartbeat's own critical path either way.
  const openclawChannelPluginsItem = await probeOpenClawChannelPlugins(
    checkedAt,
    openclawItem,
    options.openclawProfile,
    options.openclawBinaryPath,
    commandExists,
    runCommand,
  );
  const serviceInventory: PassiveServiceInventoryItem[] = [
    postgresItem,
    dockerItemResult,
    ollamaItemResult,
    codexCliItemResult,
    claudeCliItemResult,
    grokCliItemResult,
    cursorCliItemResult,
    gpuItem,
    ...(openclawItem ? [openclawItem] : []),
    ...(openclawChannelPluginsItem ? [openclawChannelPluginsItem] : []),
  ];
  // Feed the just-computed Docker probe result into the shell_sandbox
  // permission gate (runtime/desktop-permissions.ts) — same probe, no
  // separate check, no extra race between this and capability readiness.
  const dockerItem = serviceInventory.find((item) => item.id === "docker");
  setShellSandboxDockerReady(dockerItem?.status === "ready");
  // Same pattern for the on-box LLM capability: the just-computed Ollama probe
  // gates the llm_runtime permission, so llm.generate only reports ready when a
  // local Ollama endpoint is actually reachable (BYO-brain Phase 2).
  const ollamaItem = serviceInventory.find((item) => item.id === "ollama");
  setLlmRuntimeOllamaReady(ollamaItem?.status === "ready");
  // cli_subscription (Phase 3): the SAME llm_runtime permission also opens up
  // for a box where only Claude Code or only Codex is ready (no Ollama at
  // all) — otherwise llm.generate would never even be advertised on a
  // subscription-only box, and the control plane would see a misleading
  // "capability missing" instead of "claude_code isn't ready" it can act on.
  // "ready" here already means installed AND authenticated (see
  // probeClaudeCli/probeCodexCli above) — "degraded"/"missing" don't count.
  const claudeCliItem = serviceInventory.find((item) => item.id === "claude_cli");
  setLlmRuntimeClaudeCodeReady(claudeCliItem?.status === "ready");
  const codexCliItem = serviceInventory.find((item) => item.id === "codex_cli");
  setLlmRuntimeCodexReady(codexCliItem?.status === "ready");
  // xAI Grok Build / Cursor CLI addition — same "installed AND authenticated"
  // gate as the two above.
  const grokCliItem = serviceInventory.find((item) => item.id === "grok_cli");
  setLlmRuntimeGrokBuildReady(grokCliItem?.status === "ready");
  const cursorCliItem = serviceInventory.find((item) => item.id === "cursor_cli");
  setLlmRuntimeCursorReady(cursorCliItem?.status === "ready");
  if (typeof options.localRunnerReady === "boolean") {
    serviceInventory.unshift(buildLocalRunnerInventoryItem(options.localRunnerReady, checkedAt));
  }
  const permissionInventory = buildDesktopPermissionInventoryItem(requested, checkedAt, env);
  if (permissionInventory) {
    serviceInventory.push(permissionInventory);
  }
  const serviceStatuses = Object.fromEntries(
    serviceInventory.map((item) => [item.id, item.status]),
  ) as Record<string, PassiveServiceStatus>;
  const permissionStates = Object.fromEntries(
    requested
      .map((capability) => [capability, capabilityPermissionStatus(capability, env)] as const)
      .filter(([, status]) => status.state !== "not_applicable"),
  );
  const readyCapabilities = requested.filter((capability) => capabilityPermissionReady(capability, env));
  const blockedCapabilities = requested.filter((capability) => !capabilityPermissionReady(capability, env));

  const snapshot = {
    service_inventory: serviceInventory,
    native_runtime: nativeRuntime,
    capability_readiness: {
      requested,
      ready: readyCapabilities,
      blocked: blockedCapabilities,
      permission_states: permissionStates,
      passive_services: serviceInventory.map((item) => item.id),
      service_statuses: serviceStatuses,
      shell_full_access_locally_enabled: Boolean(options.shellFullAccessLocallyEnabled),
    },
  };
  const readySnapshot = typeof options.localRunnerReady === "boolean"
    ? applyLocalRunnerReadiness(snapshot, options.localRunnerReady, checkedAt)
    : snapshot;
  if (!hasCustomDeps && typeof options.localRunnerReady !== "boolean") {
    passiveInventoryCache = { key: cacheKey, capturedAtMs: Date.now(), snapshot: readySnapshot };
  }
  return readySnapshot;
}

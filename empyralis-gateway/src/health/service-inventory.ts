import { execFile } from "child_process";
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
  setLlmRuntimeOllamaReady,
  setShellSandboxDockerReady,
  type CapabilityPermissionStatus,
} from "../runtime/desktop-permissions";
import { resolveCommandPath } from "../shell/user-install-dirs";

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
  deps?: PassiveInventoryCollectorDeps;
}

const DEFAULT_COMMAND_TIMEOUT_MS = 1_500;
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

function defaultRunCommand(command: string, args: string[], timeoutMs: number): Promise<CommandResult> {
  return new Promise((resolve) => {
    execFile(command, args, { timeout: timeoutMs, windowsHide: true }, (error, stdout, stderr) => {
      const err = error as NodeJS.ErrnoException & { code?: number | string; signal?: NodeJS.Signals; killed?: boolean };
      const code = typeof err?.code === "number" ? err.code : (error ? 1 : 0);
      resolve({
        exitCode: code,
        stdout: String(stdout || ""),
        stderr: String(stderr || ""),
        signal: err?.signal ?? null,
        timedOut: Boolean(err?.killed),
      });
    });
  });
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
  const result = await runCommand(command, ["info", "--format", "{{.ServerVersion}}"], DEFAULT_COMMAND_TIMEOUT_MS);
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
  let authenticated = detectAuthPresence(
    [
      path.join(homeDir(env), ".claude", ".credentials.json"),
      path.join(homeDir(env), ".claude", "credentials.json"),
    ],
    ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"],
    env,
  );
  if (!authenticated && platform === "darwin") {
    // On macOS, Claude Code stores its OAuth token in the login Keychain, not a
    // file. `security find-generic-password` WITHOUT -w returns only the item's
    // metadata + a 0 exit status when it EXISTS — it never emits the secret.
    // This keeps the "never read credential contents" rule while making the
    // installed-vs-authenticated signal accurate on macOS boxes too.
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

  const serviceInventory = await Promise.all([
    probePostgres(checkedAt, commandExists, runCommand),
    probeDocker(checkedAt, commandExists, runCommand),
    probeOllama(checkedAt, httpGetJson),
    probeCodexCli(checkedAt, env, commandExists, runCommand),
    probeClaudeCli(checkedAt, env, platform, commandExists, runCommand),
    probeGpu(checkedAt, platform, commandExists, runCommand),
  ]);
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

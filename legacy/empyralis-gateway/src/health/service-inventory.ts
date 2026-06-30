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
  type CapabilityPermissionStatus,
} from "../runtime/desktop-permissions";

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
const LOCAL_RUNNER_CAPABILITIES: ReadonlySet<string> = new Set([
  "filesystem.read_write",
  "shell.execute",
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

function defaultCommandExists(command: string, env: NodeJS.ProcessEnv, platform: NodeJS.Platform): string | null {
  const candidates: string[] = [];
  if (path.isAbsolute(command) || command.includes("/") || command.includes("\\")) {
    candidates.push(command);
  } else {
    const pathValue = env.PATH || "";
    const extensions = platform === "win32"
      ? String(env.PATHEXT || ".EXE;.CMD;.BAT;.COM").split(";").filter(Boolean)
      : [""];
    for (const directory of pathValue.split(path.delimiter).filter(Boolean)) {
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
    metadata: { status_code: result.status, model_count: modelCount },
  }, checkedAt);
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
    }, checkedAt);
  }
  const result = await runCommand(command, ["--version"], DEFAULT_COMMAND_TIMEOUT_MS);
  const ready = result.exitCode === 0;
  return makeItem({
    id: "codex_cli",
    label: "Codex CLI",
    kind: "developer_tool",
    status: ready ? "ready" : "degraded",
    detected: true,
    check: "codex --version",
    summary: ready
      ? truncate(result.stdout || "Codex CLI is available.")
      : truncate(result.stderr || result.stdout || `codex --version exited with ${result.exitCode}.`),
    metadata: { path: command, exit_code: result.exitCode, timed_out: Boolean(result.timedOut) },
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
    probeGpu(checkedAt, platform, commandExists, runCommand),
  ]);
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

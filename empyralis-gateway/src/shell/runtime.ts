import { promises as fs } from "fs";
import path from "path";
import crypto from "crypto";
import { spawn } from "child_process";

import type { GatewayRequestEnvelope, GatewayToolInvokePayload } from "../protocol/types";
import { collectPassiveInventorySnapshot } from "../health/service-inventory";
import { checkFilesystemPathPolicy, checkShellCommandPolicy } from "./command-policy";
import { buildDockerRunArgs, DEFAULT_SANDBOX_IMAGE, DOCKER_WORKSPACE_PATH, spawnDockerRun } from "./docker-sandbox";
import { describeDockerAutostartOutcome, ensureDockerReady, type DockerAutostartOutcome } from "./docker-autostart";

const SHELL_EXECUTE_CAPABILITY = "shell.execute";
const FILESYSTEM_READ_WRITE_CAPABILITY = "filesystem.read_write";

const SUPPORTED_CAPABILITIES = [SHELL_EXECUTE_CAPABILITY, FILESYSTEM_READ_WRITE_CAPABILITY];

const DEFAULT_TIMEOUT_SECONDS = 60;
const MAX_TIMEOUT_SECONDS = 300;

export interface GatewayShellRuntimeConfig {
  stateDir: string;
  /**
   * The box operator's half of the full_access opt-in. The other half is
   * server-asserted per-call authorization (runtime_access_mode/
   * empyralis_approved/agent_scope/policy on the tool.invoke frame, which
   * gateway_execution_service.py already only sets when agent_scope is
   * exactly "sage" and the registration's persisted
   * autonomous_agent_setup_warning_acknowledged metadata is true). BOTH
   * must be true for a call to actually run in full_access mode — this
   * local flag alone does nothing without server authorization, and server
   * authorization alone does nothing without this local flag.
   */
  fullAccessLocallyEnabled: boolean;
  dockerImage?: string;
  memoryMb?: number;
  cpus?: number;
  /** Injectable for tests. Defaults to a real, cached `docker info` probe.
   *  Answers only "is Docker ready right now" — never attempts to start
   *  anything; see dockerAutostart below for that. */
  dockerReadyCheck?: () => Promise<boolean>;
  /**
   * Injectable for tests. Consulted only when dockerReadyCheck() above
   * reports not-ready, before this capability gives up. Defaults to the
   * real ensureDockerReady() (./docker-autostart.ts), which attempts to
   * start Docker — Docker Desktop on macOS, the docker service on Linux —
   * bounded, cooldown-gated, and single-flighted with every other caller in
   * this process (including the heartbeat's own background attempt in
   * cloud/ws-client.ts). Never falls back to unsandboxed host execution;
   * see resolveExecutionMode/runOnHost for why full_access is a completely
   * separate, separately-authorized path.
   */
  dockerAutostart?: () => Promise<DockerAutostartOutcome>;
}

function requireObject(value: unknown, message: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(message);
  }
  return value as Record<string, unknown>;
}

function token(value: unknown): string {
  return String(value ?? "").trim();
}

function positiveIntOr(value: unknown, fallback: number, max: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return fallback;
  }
  return Math.min(Math.floor(parsed), max);
}

/** Sanitizes a mount name into a safe directory-name component. */
function sanitizeMountName(mountName: string): string {
  const cleaned = mountName.trim().toLowerCase().replace(/[^a-z0-9_-]/g, "_");
  return cleaned || "default";
}

/** Sanitizes a workspace id into a safe directory-name component. */
function sanitizeWorkspaceId(workspaceId: string): string {
  const cleaned = workspaceId.trim().replace(/[^a-zA-Z0-9_-]/g, "_");
  return cleaned || "default";
}

export interface ExecutionModeDecision {
  mode: "sandbox" | "full_access";
  reason: string;
}

/**
 * Resolves sandbox (default, the floor) vs. full_access (opt-in) for a
 * single call. full_access requires BOTH the local box opt-in AND a
 * server-asserted authorization on the frame — the calling agent cannot
 * request escalation via its own tool-call arguments, only the resolved
 * server-side policy (set only for Sage, after the owner's setup-warning
 * acknowledgement) can assert it.
 */
export function resolveExecutionMode(
  payload: GatewayToolInvokePayload,
  config: GatewayShellRuntimeConfig,
): ExecutionModeDecision {
  if (!config.fullAccessLocallyEnabled) {
    return { mode: "sandbox", reason: "full_access is not enabled locally on this box" };
  }
  const policy = (payload.policy ?? {}) as Record<string, unknown>;
  const serverAuthorized =
    payload.runtime_access_mode === "full_access" &&
    payload.empyralis_approved === true &&
    payload.agent_scope === "sage" &&
    policy.mode === "full_access" &&
    policy.full_access_warning_acknowledged === true;
  if (!serverAuthorized) {
    return { mode: "sandbox", reason: "server did not authorize full_access for this call" };
  }
  return { mode: "full_access", reason: "owner-enabled single-agent box, authorized by the cloud control plane" };
}

/** Builds the "neither mode is usable" error for shell.execute /
 *  filesystem.read_write when sandbox mode was selected and Docker isn't
 *  ready. `decision.reason` already carries the specific reason full_access
 *  isn't active for this call — resolveExecutionMode never returns
 *  "sandbox" without setting one (either "not enabled locally on this box"
 *  or "server did not authorize full_access for this call"). Folding that
 *  in turns a dead end ("neither is available", no hint which of
 *  full_access's two required keys is missing) into an actionable message,
 *  without any change to how the two keys are resolved. */
function unavailableExecutionModeMessage(
  capabilityId: string,
  decision: ExecutionModeDecision,
  dockerDetail?: string,
): string {
  const dockerClause = dockerDetail ?? "Docker is not ready here.";
  return (
    `${capabilityId} requires Docker (sandbox mode) or an explicitly enabled and authorized ` +
    `full_access mode — neither is available on this Gateway right now. ${dockerClause} ` +
    `and full_access is not active because: ${decision.reason}.`
  );
}

async function isDockerReady(): Promise<boolean> {
  const snapshot = await collectPassiveInventorySnapshot({});
  return snapshot.capability_readiness.service_statuses.docker === "ready";
}

export class GatewayShellRuntime {
  private readonly dockerReadyCheck: () => Promise<boolean>;
  private readonly dockerAutostart: () => Promise<DockerAutostartOutcome>;

  constructor(private readonly config: GatewayShellRuntimeConfig) {
    this.dockerReadyCheck = config.dockerReadyCheck ?? isDockerReady;
    this.dockerAutostart = config.dockerAutostart ?? (() => ensureDockerReady());
  }

  /**
   * Is Docker usable for this call — and if not, was starting it able to
   * fix that. Never throws; never falls back to unsandboxed execution. The
   * founder's own framing is the reason this exists: "while a user is
   * running this thing they won't have any agents to keep everything
   * fixed" — so before refusing sandbox mode entirely, the gateway gets one
   * bounded, self-contained attempt to fix the one thing that's actually
   * broken.
   */
  private async ensureDockerAvailable(): Promise<{ ready: true } | { ready: false; detail: string }> {
    if (await this.dockerReadyCheck()) {
      return { ready: true };
    }
    const outcome = await this.dockerAutostart();
    if (outcome.kind === "already_ready" || outcome.kind === "started") {
      return { ready: true };
    }
    return { ready: false, detail: describeDockerAutostartOutcome(outcome) };
  }

  requestedCapabilities(): string[] {
    return [...SUPPORTED_CAPABILITIES];
  }

  supportsCapability(capabilityId: string): boolean {
    return SUPPORTED_CAPABILITIES.includes(String(capabilityId ?? "").trim());
  }

  async handleCapabilityInvoke(
    frame: GatewayRequestEnvelope<GatewayToolInvokePayload>,
  ): Promise<Record<string, unknown>> {
    const payload = frame.payload;
    const capabilityId = token(payload.capability_id);
    const argumentsPayload = requireObject(payload.arguments ?? {}, "arguments must be an object.");
    const workspaceId = sanitizeWorkspaceId(token(payload.workspace_id) || "default");
    const mountName = sanitizeMountName(token(argumentsPayload.mount) || "default");
    const workspaceHostPath = await this.ensureWorkspaceDir(mountName, workspaceId);
    const timeoutSeconds = positiveIntOr(argumentsPayload.timeout_seconds, DEFAULT_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS);
    const decision = resolveExecutionMode(payload, this.config);

    if (capabilityId === SHELL_EXECUTE_CAPABILITY) {
      return this.executeShell(argumentsPayload, workspaceHostPath, timeoutSeconds, decision);
    }
    if (capabilityId === FILESYSTEM_READ_WRITE_CAPABILITY) {
      return this.executeFilesystem(argumentsPayload, workspaceHostPath, timeoutSeconds, decision);
    }
    throw new Error(`Unsupported shell_sandbox capability: ${capabilityId || "unknown"}`);
  }

  private async ensureWorkspaceDir(mountName: string, workspaceId: string): Promise<string> {
    const workspaceHostPath = path.join(this.config.stateDir, "mounts", mountName, workspaceId);
    await fs.mkdir(workspaceHostPath, { recursive: true });
    return workspaceHostPath;
  }

  private async executeShell(
    argumentsPayload: Record<string, unknown>,
    workspaceHostPath: string,
    timeoutSeconds: number,
    decision: ExecutionModeDecision,
  ): Promise<Record<string, unknown>> {
    const command = token(argumentsPayload.command);
    if (!command) {
      throw new Error("command is required");
    }
    const policyBlock = checkShellCommandPolicy(command, workspaceHostPath);
    if (policyBlock) {
      throw new Error(policyBlock.message);
    }

    if (decision.mode === "sandbox") {
      const availability = await this.ensureDockerAvailable();
      if (!availability.ready) {
        throw new Error(unavailableExecutionModeMessage(SHELL_EXECUTE_CAPABILITY, decision, availability.detail));
      }
      const containerName = `empyralis-shell-${crypto.randomUUID()}`;
      const args = buildDockerRunArgs({
        image: this.config.dockerImage || DEFAULT_SANDBOX_IMAGE,
        workspaceHostPath,
        innerArgs: ["/bin/sh", "-lc", command],
        memoryMb: this.config.memoryMb,
        cpus: this.config.cpus,
        containerName,
      });
      const result = await spawnDockerRun(args, { timeoutMs: timeoutSeconds * 1000 });
      return {
        command,
        exit_code: result.exitCode,
        stdout: result.stdout.trim(),
        stderr: result.stderr.trim(),
        timed_out: result.timedOut,
        execution_mode: "sandbox",
        sandbox: {
          mode: "docker",
          workspace_kind: "ephemeral_container",
          container_name: containerName,
          read_only_base_image: true,
          network_policy: { mode: "none" },
        },
      };
    }

    // full_access mode: runs directly on the host. Still passed the same
    // hard command/path policy check above — that check is never bypassed
    // in any mode.
    const result = await runOnHost(command, workspaceHostPath, timeoutSeconds);
    return {
      command,
      exit_code: result.exitCode,
      stdout: result.stdout.trim(),
      stderr: result.stderr.trim(),
      timed_out: result.timedOut,
      execution_mode: "full_access",
      warning:
        "This command ran with FULL HOST ACCESS, not sandboxed — this agent can affect the entire machine, not just a scoped workspace.",
    };
  }

  private async executeFilesystem(
    argumentsPayload: Record<string, unknown>,
    workspaceHostPath: string,
    timeoutSeconds: number,
    decision: ExecutionModeDecision,
  ): Promise<Record<string, unknown>> {
    const relativePath = token(argumentsPayload.path || argumentsPayload.file_path);
    if (!relativePath) {
      throw new Error("path is required");
    }
    const mode = token(argumentsPayload.mode) || "read";
    const content = String(argumentsPayload.content ?? "");

    // Hard-protected-path check runs against the fully resolved target,
    // in BOTH modes. In sandbox mode this is defense-in-depth on top of the
    // container boundary itself; in full_access mode it is the only thing
    // standing between "full machine access" and the credential vault.
    const absoluteTargetForPolicy = path.isAbsolute(relativePath)
      ? relativePath
      : path.join(workspaceHostPath, relativePath);
    const policyBlock = checkFilesystemPathPolicy(absoluteTargetForPolicy);
    if (policyBlock) {
      throw new Error(policyBlock.message);
    }

    if (decision.mode === "sandbox") {
      const availability = await this.ensureDockerAvailable();
      if (!availability.ready) {
        throw new Error(unavailableExecutionModeMessage(FILESYSTEM_READ_WRITE_CAPABILITY, decision, availability.detail));
      }
      const containerName = `empyralis-fs-${crypto.randomUUID()}`;
      const innerArgs = filesystemInnerArgs(mode, relativePath);
      const args = buildDockerRunArgs({
        image: this.config.dockerImage || DEFAULT_SANDBOX_IMAGE,
        workspaceHostPath,
        innerArgs,
        memoryMb: this.config.memoryMb,
        cpus: this.config.cpus,
        containerName,
        // Reads/writes touch the bind-mounted /workspace only; the
        // container's own root stays read-only regardless.
      });
      const result = await spawnDockerRun(args, {
        timeoutMs: timeoutSeconds * 1000,
        stdin: mode === "write" || mode === "append" ? content : undefined,
      });
      if (result.exitCode !== 0) {
        throw new Error(result.stderr.trim() || `filesystem.read_write exited with code ${result.exitCode}`);
      }
      return {
        path: relativePath,
        mode,
        content: mode === "read" ? result.stdout : undefined,
        execution_mode: "sandbox",
        sandbox: { mode: "docker", workspace_kind: "ephemeral_container", container_name: containerName },
      };
    }

    // full_access mode: direct host filesystem I/O, still policy-checked above.
    const targetPath = absoluteTargetForPolicy;
    if (mode === "read") {
      const fileContent = await fs.readFile(targetPath, "utf8");
      return {
        path: relativePath,
        mode,
        content: fileContent,
        execution_mode: "full_access",
        warning: "This file was read with FULL HOST ACCESS, not a sandboxed workspace mount.",
      };
    }
    await fs.mkdir(path.dirname(targetPath), { recursive: true });
    if (mode === "append") {
      await fs.appendFile(targetPath, content, "utf8");
    } else {
      await fs.writeFile(targetPath, content, "utf8");
    }
    return {
      path: relativePath,
      mode,
      execution_mode: "full_access",
      warning: "This file was written with FULL HOST ACCESS, not a sandboxed workspace mount.",
    };
  }
}

function filesystemInnerArgs(mode: string, relativePath: string): string[] {
  if (mode === "write") {
    return ["/bin/sh", "-c", 'cat > "$1"', "write-cmd", relativePath];
  }
  if (mode === "append") {
    return ["/bin/sh", "-c", 'cat >> "$1"', "append-cmd", relativePath];
  }
  return ["/bin/sh", "-c", 'cat -- "$1"', "read-cmd", relativePath];
}

interface HostRunResult {
  exitCode: number | null;
  stdout: string;
  stderr: string;
  timedOut: boolean;
}

/** Direct host execution for full_access mode — no container, no isolation. */
function runOnHost(command: string, cwd: string, timeoutSeconds: number): Promise<HostRunResult> {
  return new Promise((resolve, reject) => {
    const child = spawn("/bin/sh", ["-lc", command], { cwd, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    let settled = false;
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGKILL");
    }, timeoutSeconds * 1000);
    timer.unref?.();
    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString("utf8");
    });
    child.on("error", (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(error);
    });
    child.on("close", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ exitCode: code, stdout, stderr, timedOut });
    });
  });
}

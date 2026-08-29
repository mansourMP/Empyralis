import { promises as fs } from "fs";
import path from "path";
import crypto from "crypto";
import { spawn } from "child_process";

import type { GatewayRequestEnvelope, GatewayToolInvokePayload } from "../protocol/types";
import { collectPassiveInventorySnapshot } from "../health/service-inventory";
import { checkFilesystemPathPolicy, checkShellCommandPolicy } from "./command-policy";
import { buildDockerRunArgs, DEFAULT_SANDBOX_IMAGE, DOCKER_WORKSPACE_PATH, spawnDockerRun } from "./docker-sandbox";
import { describeDockerAutostartOutcome, ensureDockerReady, type DockerAutostartOutcome } from "./docker-autostart";
import { resolveExecution, type ResolvedExecution } from "./execution-isolation";
import { diagnoseKill, killDiagnosisFields } from "./kill-diagnosis";
import {
  buildBatchDriverScript,
  computeBatchTimeoutSeconds,
  interpretBatchResults,
  parseBatchCommandsArgument,
  type BatchCommandSpec,
  type RawCommandFileState,
} from "./batch-shell";

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
   * cloud/ws-client.ts). When it loses, the call now runs on the host in
   * `host` mode rather than failing — see execution-isolation.ts, and note
   * that `host` is NOT the separately-authorized `full_access` escalation.
   */
  dockerAutostart?: () => Promise<DockerAutostartOutcome>;
  /**
   * Injectable for tests. Defaults to the real `process.platform`.
   *
   * Exists because macOS deliberately never uses Docker at all (see
   * ensureDockerAvailable), and that rule has to be TESTABLE from a test
   * process that is itself running on macOS. Reading `process.platform`
   * inline made the Linux sandbox tests fail on a developer's Mac while the
   * production Linux path was fine — an untestable branch, which is exactly
   * the shape this codebase keeps getting caught by.
   */
  platform?: NodeJS.Platform;
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
 * Resolves the AUTHORIZATION question only: has the owner deliberately put
 * this box into full_access, or not. It does NOT look at Docker and does
 * not decide isolation — resolveRun()/execution-isolation.ts does that,
 * from this answer plus live sandbox availability.
 *
 * full_access still requires BOTH the local box opt-in AND a server-asserted
 * authorization on the frame — the calling agent cannot request escalation
 * via its own tool-call arguments, only the resolved server-side policy can
 * assert it. That two-part opt-in is UNCHANGED by the 2026-08-22 "Docker is
 * not a wall" work: a Docker-less box runs in `host` mode, which grants no
 * full_access policy and claims no authorization it was never given.
 *
 * "sandbox" returned here therefore means "not authorized to escalate",
 * never "a container is definitely available".
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

async function isDockerReady(): Promise<boolean> {
  const snapshot = await collectPassiveInventorySnapshot({});
  return snapshot.capability_readiness.service_statuses.docker === "ready";
}

export class GatewayShellRuntime {
  private readonly dockerReadyCheck: () => Promise<boolean>;
  private readonly dockerAutostart: () => Promise<DockerAutostartOutcome>;
  private readonly platform: NodeJS.Platform;

  constructor(private readonly config: GatewayShellRuntimeConfig) {
    this.dockerReadyCheck = config.dockerReadyCheck ?? isDockerReady;
    this.dockerAutostart = config.dockerAutostart ?? (() => ensureDockerReady());
    this.platform = config.platform ?? process.platform;
  }

  /**
   * Is Docker usable for this call — and if not, was starting it able to
   * fix that. Never throws. The founder's own framing is the reason this
   * exists: "while a user is running this thing they won't have any agents
   * to keep everything fixed" — so before concluding this box has no
   * sandbox, the gateway gets one bounded, self-contained attempt to fix
   * the one thing that's actually broken.
   *
   * What CHANGED on 2026-08-22 is only what happens when it loses: the
   * answer used to be a thrown dead end, and is now a host run that is
   * labelled as one (resolveRun below). This function is unchanged.
   */
  private async ensureDockerAvailable(): Promise<{ ready: true } | { ready: false; detail: string }> {
    // MACOS DOES NOT USE DOCKER AT ALL. Founder's decision, 2026-08-26, asked
    // as an explicit choice and answered "never use it, always run direct":
    // *"I don't need no fucking Docker here."*
    //
    // Deliberately BEFORE the readiness probe, not after — the point is that
    // Docker is irrelevant on a personal Mac, so we do not even ask whether
    // it is running. Docker being open must not silently change how an
    // agent behaves; that inconsistency (capability depending on whether an
    // app happens to be running) is itself a thing he has been bitten by.
    //
    // A separate change already stopped macOS LAUNCHING Docker
    // (resolvePlatformStartCommand -> null). This is the second half: it is
    // never USED either. Together they mean one behaviour on a Mac, always —
    // commands run on the machine.
    //
    // Linux is untouched and still prefers the sandbox: a headless daemon on
    // a box that exists to run the agent was never the complaint.
    if (this.platform === "darwin") {
      return {
        ready: false,
        detail: "This computer runs commands directly rather than in a container.",
      };
    }
    if (await this.dockerReadyCheck()) {
      return { ready: true };
    }
    const outcome = await this.dockerAutostart();
    if (outcome.kind === "already_ready" || outcome.kind === "started") {
      return { ready: true };
    }
    return { ready: false, detail: describeDockerAutostartOutcome(outcome) };
  }

  /**
   * Turns the AUTHORIZATION decision (resolveExecutionMode, which never
   * looks at Docker) plus live sandbox availability into the one thing the
   * executors below branch on. This is the seam the founder's 2026-08-22
   * ruling changed: it used to be able to end in a thrown "neither mode is
   * available" dead end, and now it always returns a way to run.
   *
   * The bounded, cooldown-gated, single-flighted Docker autostart attempt
   * still happens first and is unchanged — trying to start the sandbox
   * before concluding it is absent is correct, and a box whose Docker was
   * merely asleep still gets a container rather than a host run.
   */
  private async resolveRun(decision: ExecutionModeDecision): Promise<ResolvedExecution> {
    if (decision.mode === "full_access") {
      return resolveExecution("full_access", false);
    }
    const availability = await this.ensureDockerAvailable();
    return availability.ready
      ? resolveExecution("sandbox", true)
      : resolveExecution("sandbox", false, availability.detail);
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
      // Batch dispatch: a `commands` array on an ordinary shell.execute call
      // signals "run several commands in one round trip" — no new
      // capability_id, so every existing authorization/permission/approval
      // check keyed on "shell.execute" (cloud-side and here) covers it for
      // free. `command` (singular) and `commands` (plural) are mutually
      // exclusive: a caller sending both almost certainly built the payload
      // wrong, and guessing which one they meant would silently do the
      // wrong thing instead of telling them.
      const hasCommands = Array.isArray(argumentsPayload.commands) && argumentsPayload.commands.length > 0;
      const hasCommand = token(argumentsPayload.command).length > 0;
      if (hasCommands && hasCommand) {
        throw new Error("shell.execute received both `command` and `commands` — provide exactly one, not both.");
      }
      if (hasCommands) {
        return this.executeShellBatch(argumentsPayload, workspaceHostPath, decision);
      }
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

    const execution = await this.resolveRun(decision);

    if (execution.mode === "sandbox") {
      const containerName = `empyralis-shell-${crypto.randomUUID()}`;
      const args = buildDockerRunArgs({
        image: this.config.dockerImage || DEFAULT_SANDBOX_IMAGE,
        workspaceHostPath,
        innerArgs: ["/bin/sh", "-lc", command],
        memoryMb: this.config.memoryMb,
        cpus: this.config.cpus,
        containerName,
      });
      const result = await spawnDockerRun(args, {
        timeoutMs: timeoutSeconds * 1000,
        containerNameForTimeoutKill: containerName,
      });
      return {
        command,
        exit_code: result.exitCode,
        stdout: result.stdout.trim(),
        stderr: result.stderr.trim(),
        timed_out: result.timedOut,
        // A container the cgroup limit killed exits 137 and writes nothing.
        // Without this the agent is handed that number and no reason for it —
        // and a real annual ledger is exactly the size that produces it.
        ...killDiagnosisFields(
          diagnoseKill({
            exitCode: result.exitCode,
            timedOut: result.timedOut,
            isolation: "sandbox",
            memoryLimitMb: this.config.memoryMb,
          }),
        ),
        execution_mode: "sandbox",
        isolation: execution.isolation,
        isolation_statement: execution.statement,
        sandbox: {
          mode: "docker",
          workspace_kind: "ephemeral_container",
          container_name: containerName,
          read_only_base_image: true,
          network_policy: { mode: "none" },
        },
      };
    }

    // host / full_access: runs directly on the machine. Still passed the
    // same hard command/path policy check above — that check is never
    // bypassed in any mode, and it is what keeps the vault, ~/.ssh and the
    // catastrophic-command list out of reach on a Docker-less box.
    const result = await runOnHost(command, workspaceHostPath, timeoutSeconds);
    return {
      command,
      exit_code: result.exitCode,
      stdout: result.stdout.trim(),
      stderr: result.stderr.trim(),
      timed_out: result.timedOut,
      // On the host a killed command reports exit_code null with an empty
      // stderr — literally nothing. No memory limit is named here because we
      // set none; inventing one would be fabricating the number the whole
      // diagnosis rests on.
      ...killDiagnosisFields(
        diagnoseKill({
          exitCode: result.exitCode,
          signal: result.signal,
          timedOut: result.timedOut,
          isolation: "host",
        }),
      ),
      execution_mode: execution.mode,
      isolation: execution.isolation,
      isolation_statement: execution.statement,
      // `warning` stays EXCLUSIVE to full_access: that is a deliberate
      // escalation the owner turned on, and it is worth flagging every
      // time. A `host` run is the ordinary state of a computer without
      // Docker, so it gets the neutral statement above and no alarm —
      // the founder's own framing: two true statements, not an error.
      ...(execution.mode === "full_access"
        ? {
            warning:
              "This command ran with FULL HOST ACCESS, not sandboxed — this agent can affect the entire machine, not just a scoped workspace.",
          }
        : {}),
    };
  }

  /**
   * shell.execute with a `commands` array: gates every command up front,
   * then runs them SEQUENTIALLY in one shared container (sandbox mode) or
   * one shared host shell process (full_access mode) — see batch-shell.ts's
   * module header for the full isolation-semantics writeup (state IS
   * shared within a batch, by design; separate batches/calls stay fully
   * isolated from each other exactly as today).
   */
  private async executeShellBatch(
    argumentsPayload: Record<string, unknown>,
    workspaceHostPath: string,
    decision: ExecutionModeDecision,
  ): Promise<Record<string, unknown>> {
    const specs = parseBatchCommandsArgument(argumentsPayload.commands, {
      defaultTimeoutSeconds: positiveIntOr(argumentsPayload.timeout_seconds, DEFAULT_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS),
    });
    const stopOnFailure = argumentsPayload.stop_on_failure === undefined ? true : Boolean(argumentsPayload.stop_on_failure);

    // Gate EVERY command before executing ANY of them. Half-executing a
    // batch and then refusing is the worst outcome — it mutates state and
    // then reports failure. A refusal here throws (matching the existing
    // single-command policy-violation contract) and NOTHING below this
    // point ever runs: no directory is created, no container is started.
    const violations: string[] = [];
    for (const spec of specs) {
      const policyBlock = checkShellCommandPolicy(spec.command, workspaceHostPath);
      if (policyBlock) {
        violations.push(`commands[${spec.index}]: ${policyBlock.message}`);
      }
    }
    if (violations.length > 0) {
      throw new Error(
        `Batch refused — ${violations.length} of ${specs.length} command(s) violate policy, so none of them ran:\n` +
          violations.join("\n"),
      );
    }

    const batchTimeoutSeconds = computeBatchTimeoutSeconds(specs);
    const batchId = crypto.randomUUID();
    const hostDir = path.join(workspaceHostPath, ".empyralis-batch", batchId);
    await fs.mkdir(hostDir, { recursive: true });
    for (const spec of specs) {
      const body = spec.command.endsWith("\n") ? spec.command : `${spec.command}\n`;
      await fs.writeFile(path.join(hostDir, `cmd_${spec.index}.sh`), body, "utf8");
    }

    // Two timeout layers, deliberately different mechanisms:
    //   1. PRIMARY: the watchdog below polls for the one command currently
    //      "started" and aborts the whole batch the moment IT (not the
    //      batch as a whole) has run longer than its own timeout_seconds —
    //      this is what makes a per-command timeout actually behave
    //      per-command, at ~watchdog-poll-interval granularity.
    //   2. BACKSTOP: batchTimeoutSeconds (the sum of every command's own
    //      budget, capped) is still passed as the outer hard deadline to
    //      spawnDockerRun/runBatchOnHost — safety net only, for the case
    //      where the watchdog itself can't observe progress (e.g. the
    //      driver script hangs before writing its very first status file).
    //      In the ordinary case the watchdog fires first, well before this
    //      ever would.
    const controller = new AbortController();
    let watchdogFiredIndex: number | null = null;
    const watchdog = watchBatchCommandsForOverrun(hostDir, specs, (index) => {
      watchdogFiredIndex = index;
      controller.abort();
    });

    try {
      const execution = await this.resolveRun(decision);
      if (execution.mode === "sandbox") {
        const containerDir = `${DOCKER_WORKSPACE_PATH}/.empyralis-batch/${batchId}`;
        const driverScript = buildBatchDriverScript(specs, { stopOnFailure, batchDirPath: containerDir });
        await fs.writeFile(path.join(hostDir, "driver.sh"), driverScript, "utf8");
        const containerName = `empyralis-shell-batch-${batchId}`;
        const args = buildDockerRunArgs({
          image: this.config.dockerImage || DEFAULT_SANDBOX_IMAGE,
          workspaceHostPath,
          innerArgs: ["/bin/sh", `${containerDir}/driver.sh`],
          memoryMb: this.config.memoryMb,
          cpus: this.config.cpus,
          containerName,
        });
        const dockerResult = await spawnDockerRun(args, {
          timeoutMs: batchTimeoutSeconds * 1000,
          containerNameForTimeoutKill: containerName,
          signal: controller.signal,
        });
        watchdog.stop();
        const batchTimedOut = dockerResult.timedOut || watchdogFiredIndex !== null;
        const states = await readBatchFileStates(hostDir, specs);
        const { results, stoppedEarly } = interpretBatchResults(specs, states, {
          stopOnFailure,
          batchTimedOut,
          isolation: "sandbox",
          memoryLimitMb: this.config.memoryMb,
        });
        return {
          commands: results,
          execution_mode: "sandbox",
          isolation: execution.isolation,
          isolation_statement: execution.statement,
          stop_on_failure: stopOnFailure,
          stopped_early: stoppedEarly,
          batch_timed_out: batchTimedOut,
          batch_timeout_seconds: batchTimeoutSeconds,
          sandbox: {
            mode: "docker",
            workspace_kind: "ephemeral_container",
            container_name: containerName,
            read_only_base_image: true,
            network_policy: { mode: "none" },
            shared_container_note:
              "All commands in this batch ran in ONE container and therefore share filesystem/cwd/env with each other " +
              "(cd/export from one command carries into the next) — a separate batch call always gets a fresh container.",
          },
        };
      }

      // host / full_access: same driver script, run directly on the machine.
      const driverScript = buildBatchDriverScript(specs, { stopOnFailure, batchDirPath: hostDir });
      const driverPath = path.join(hostDir, "driver.sh");
      await fs.writeFile(driverPath, driverScript, "utf8");
      const hostResult = await runBatchOnHost(driverPath, workspaceHostPath, batchTimeoutSeconds, controller.signal);
      watchdog.stop();
      const batchTimedOut = hostResult.timedOut || watchdogFiredIndex !== null;
      const states = await readBatchFileStates(hostDir, specs);
      const { results, stoppedEarly } = interpretBatchResults(specs, states, {
        stopOnFailure,
        batchTimedOut,
        isolation: "host",
      });
      return {
        commands: results,
        execution_mode: execution.mode,
        isolation: execution.isolation,
        isolation_statement: execution.statement,
        stop_on_failure: stopOnFailure,
        stopped_early: stoppedEarly,
        batch_timed_out: batchTimedOut,
        batch_timeout_seconds: batchTimeoutSeconds,
        // See executeShell above: `warning` is full_access-only on purpose.
        ...(execution.mode === "full_access"
          ? {
              warning:
                "This batch ran with FULL HOST ACCESS, not sandboxed — this agent can affect the entire machine, not just a scoped workspace.",
            }
          : {}),
      };
    } finally {
      watchdog.stop();
      // Best-effort cleanup — AWAITED (a small directory of a handful of
      // tiny files is fast to remove, so this costs the caller nothing
      // meaningful) so the scratch directory is reliably gone by the time
      // this call returns OR throws, rather than racing whoever inspects
      // the workspace mount next. A cleanup failure itself is swallowed —
      // it must never mask the real result/error this function is already
      // returning or throwing, and a leftover directory is harmless on its
      // own (the next batch on this workspace gets its own uuid-named one).
      try {
        await fs.rm(hostDir, { recursive: true, force: true });
      } catch {
        // Nothing to do.
      }
    }
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

    const execution = await this.resolveRun(decision);

    if (execution.mode === "sandbox") {
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
        // A read of a file bigger than the container's memory limit is killed
        // by the cgroup and exits 137 having written nothing at all, so the
        // fallback message here used to be the entire diagnosis: "exited with
        // code 137". This throw IS what the model reads, so the reason goes
        // in it rather than only onto a result field this branch never
        // returns.
        const killed = diagnoseKill({
          exitCode: result.exitCode,
          timedOut: result.timedOut,
          isolation: "sandbox",
          memoryLimitMb: this.config.memoryMb,
        });
        if (killed) throw new Error(killed.statement);
        throw new Error(result.stderr.trim() || `filesystem.read_write exited with code ${result.exitCode}`);
      }
      return {
        path: relativePath,
        mode,
        content: mode === "read" ? result.stdout : undefined,
        execution_mode: "sandbox",
        isolation: execution.isolation,
        isolation_statement: execution.statement,
        sandbox: { mode: "docker", workspace_kind: "ephemeral_container", container_name: containerName },
      };
    }

    // host / full_access: direct filesystem I/O on the machine, still
    // policy-checked above — checkFilesystemPathPolicy is the only thing
    // between this branch and the credential vault, and it runs in every
    // mode before this decision is consulted.
    const targetPath = absoluteTargetForPolicy;
    const fullAccessWarning =
      execution.mode === "full_access"
        ? { warning: "This file was reached with FULL HOST ACCESS, not a sandboxed workspace mount." }
        : {};
    if (mode === "read") {
      const fileContent = await fs.readFile(targetPath, "utf8");
      return {
        path: relativePath,
        mode,
        content: fileContent,
        execution_mode: execution.mode,
        isolation: execution.isolation,
        isolation_statement: execution.statement,
        ...fullAccessWarning,
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
      execution_mode: execution.mode,
      isolation: execution.isolation,
      isolation_statement: execution.statement,
      ...fullAccessWarning,
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
  /** The signal that killed it, when there was one. Node hands this to the
   *  close handler alongside the code and it used to be dropped on the floor
   *  — which is why a command the system killed reported `exit_code: null`,
   *  an empty stderr and nothing else. It is the only thing that explains
   *  that row. See kill-diagnosis.ts. */
  signal: NodeJS.Signals | null;
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
    child.on("close", (code, signal) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ exitCode: code, signal, stdout, stderr, timedOut });
    });
  });
}

interface HostBatchRunResult {
  timedOut: boolean;
}

/**
 * Runs a batch driver script directly on the host (full_access mode).
 * Unlike runOnHost above (unchanged, still a single `/bin/sh -lc <command>`
 * child), this spawns the driver DETACHED into its own process group and,
 * on timeout, kills the whole group (`process.kill(-pid, "SIGKILL")`) —
 * necessary here because a batch driver's own child (an external command
 * inside one of the sourced cmd_i.sh files, e.g. a hung `curl`) is a
 * grandchild of THIS function's spawned process, and killing only the
 * direct child would orphan it. The driver's own stdout/stderr carry
 * nothing meaningful (every command's output is redirected into its own
 * file) and are not returned — only whether the caller's deadline fired.
 */
function runBatchOnHost(
  driverPath: string,
  cwd: string,
  timeoutSeconds: number,
  signal?: AbortSignal,
): Promise<HostBatchRunResult> {
  return new Promise((resolve, reject) => {
    const child = spawn("/bin/sh", [driverPath], { cwd, stdio: ["ignore", "ignore", "ignore"], detached: true });
    let timedOut = false;
    let settled = false;
    const killNow = (): void => {
      timedOut = true;
      try {
        if (typeof child.pid === "number") {
          process.kill(-child.pid, "SIGKILL");
        } else {
          child.kill("SIGKILL");
        }
      } catch {
        // The process (or group) may already be gone between the timer
        // firing and this call — nothing more to do.
        try {
          child.kill("SIGKILL");
        } catch {
          // Same.
        }
      }
    };
    const timer = setTimeout(killNow, timeoutSeconds * 1000);
    timer.unref?.();
    const onAbort = (): void => killNow();
    if (signal) {
      if (signal.aborted) {
        killNow();
      } else {
        signal.addEventListener("abort", onAbort, { once: true });
      }
    }
    const cleanup = (): void => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    };
    child.on("error", (error) => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(error);
    });
    child.on("close", () => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve({ timedOut });
    });
  });
}

/**
 * Polls (default every 200ms) each command's status file for the ONE
 * command currently "started" and calls `onOverrun` the moment it has been
 * running longer than ITS OWN declared timeoutSeconds — this is what makes
 * a per-command timeout actually mean per-command, at roughly poll-interval
 * granularity, rather than only ever enforced at the whole batch's
 * (much larger, summed) outer deadline. See batch-shell.ts's module header
 * for why enforcement has to work this way — a shared, non-forking shell
 * process can be killed, but not interrupted one foreground statement at a
 * time without losing the very state-sharing batching exists to provide.
 */
function watchBatchCommandsForOverrun(
  hostDir: string,
  specs: BatchCommandSpec[],
  onOverrun: (index: number) => void,
  pollIntervalMs = 200,
): { stop: () => void } {
  const startedAtMs = new Map<number, number>();
  const firedFor = new Set<number>();
  let stopped = false;

  const tick = async (): Promise<void> => {
    if (stopped) {
      return;
    }
    for (const spec of specs) {
      if (firedFor.has(spec.index)) {
        continue;
      }
      let status = "";
      try {
        status = (await fs.readFile(path.join(hostDir, `status_${spec.index}`), "utf8")).trim();
      } catch {
        continue; // not written yet
      }
      if (status !== "started") {
        continue; // not yet running, or already finished/skipped
      }
      const startedAt = startedAtMs.get(spec.index);
      if (startedAt === undefined) {
        // First time we've observed this command running — start its clock
        // now rather than guessing when it actually began (at most one poll
        // interval of slop, which is what the whole mechanism trades for
        // never forking the command it's timing).
        startedAtMs.set(spec.index, Date.now());
        continue;
      }
      if (Date.now() - startedAt >= spec.timeoutSeconds * 1000) {
        firedFor.add(spec.index);
        onOverrun(spec.index);
        return;
      }
    }
  };

  const interval = setInterval(() => {
    tick().catch(() => {
      // A transient read failure just means "try again next tick" — never
      // let it throw out of a timer callback.
    });
  }, pollIntervalMs);
  interval.unref?.();
  return {
    stop: () => {
      stopped = true;
      clearInterval(interval);
    },
  };
}

/** Reads back whatever the batch driver script left on disk for each
 *  command — used whether the driver finished cleanly or was killed
 *  mid-run. A missing file is not an error here; it is itself the signal
 *  that a command never started (see interpretBatchResults). */
async function readBatchFileStates(
  hostDir: string,
  specs: BatchCommandSpec[],
): Promise<Map<number, RawCommandFileState>> {
  const states = new Map<number, RawCommandFileState>();
  await Promise.all(
    specs.map(async (spec) => {
      const readIfPresent = async (name: string): Promise<string> => {
        try {
          return await fs.readFile(path.join(hostDir, name), "utf8");
        } catch {
          return "";
        }
      };
      const [statusRaw, codeRaw, stdout, stderr] = await Promise.all([
        readIfPresent(`status_${spec.index}`),
        readIfPresent(`code_${spec.index}`),
        readIfPresent(`out_${spec.index}`),
        readIfPresent(`err_${spec.index}`),
      ]);
      const status = statusRaw.trim();
      const normalizedStatus: RawCommandFileState["status"] =
        status === "started" || status === "done" || status === "skipped" ? status : "";
      const codeTrimmed = codeRaw.trim();
      const exitCode = codeTrimmed && /^-?\d+$/.test(codeTrimmed) ? Number(codeTrimmed) : null;
      states.set(spec.index, {
        status: normalizedStatus,
        exitCode,
        stdout: stdout.trim(),
        stderr: stderr.trim(),
      });
    }),
  );
  return states;
}

import { promises as fsp } from "fs";
import path from "path";

import type { GatewayRequestEnvelope, GatewayToolInvokePayload } from "../protocol/types";
import {
  downloadGatewayArtifact,
  installGatewayArtifact,
  resolvePreviousEntrypoint,
  rollbackCurrentGatewaySymlink,
  swapCurrentGatewaySymlink,
  defaultDownloadStagingDir,
  type ExecLike,
  type GatewayFetchLike,
} from "./gateway-artifact-installer";
import { resolveGatewayReleaseLayout, type GatewayReleaseLayout } from "./gateway-release-layout";
import {
  detectGatewaySupervisor,
  spawnGatewayRestartHandoff,
  type GatewaySupervisorMode,
} from "./gateway-restart-handoff";

/**
 * Platform-triggered gateway self-update. The one `gateway.self_update`
 * capability the backend dispatches over the gateway's existing outbound
 * cloud WS (server_modules/gateway_self_update_service.trigger_gateway_self_
 * update -> gateway_execution_service.execute_tool_via_gateway -> here, the
 * identical transport cli.install and channel.imessage.personal.recheck/
 * install already use — see capability-router.ts's constructor wiring).
 *
 * Flow on invoke:
 *   1. No-op-safe check: target_version === the version this process was
 *      compiled with -> return immediately, nothing touched.
 *   2. Download the artifact tarball (gateway-artifact-installer.ts),
 *      verifying size and, when a checksum is available, its sha256.
 *   3. Extract + atomically stage it as `releasesDir/<version>` — a bad
 *      extract never becomes visible at that path (installGatewayArtifact
 *      builds it under a `.staging-*` name first, then renames it into
 *      place only after validating dist/index.js exists).
 *   4. Atomically swap the `current` symlink to the new release.
 *   5. Restart handoff: systemd (Restart=always, install-agent-computer.sh:
 *      379) just needs a clean exit; anything else spawns a detached
 *      handoff process that waits for THIS process to exit, launches the
 *      new build, and rolls back + relaunches the previous build if the new
 *      one doesn't survive a grace period (gateway-restart-handoff.ts).
 *   6. Only once the handoff is either unnecessary (supervised) or
 *      *confirmed spawned* (a PID came back) does this call the shutdown
 *      callback the index.ts wiring provided — reusing the SAME clean-
 *      shutdown path SIGINT/SIGTERM already use (journal flush + lock
 *      release), not a second exit code path. If the handoff can't be
 *      confirmed spawned, the symlink swap is rolled back and the invoke
 *      fails with the process still fully running the OLD build in memory —
 *      never a silent half-updated state.
 *
 * BOOTSTRAP NOTE for an existing 0.1.0 gateway (see final report for the
 * full explanation): this only takes effect once something actually
 * *launches* the gateway through `installRoot/current/gateway/dist/index.js`
 * — a plain `node dist/index.js` from a fixed checkout, or the current
 * production run-gateway script pointed at the OLD root-owned install path,
 * will never see a swap this code makes. That one-time launcher pointer is
 * the bootstrap step; this runtime cannot bootstrap itself into that
 * position from inside a capability invoke.
 */
export const GATEWAY_SELF_UPDATE_CAPABILITY = "gateway.self_update";

function requireNonEmptyString(value: unknown, label: string): string {
  const token = String(value ?? "").trim();
  if (!token) {
    throw new Error(`${label} is required.`);
  }
  return token;
}

export interface GatewaySelfUpdateRuntimeOptions {
  /** The version this running process was built with — GATEWAY_VERSION in
   *  index.ts. Used for the no-op-safe check and returned in every result. */
  currentVersion: string;
  stateDir: string;
  env?: NodeJS.ProcessEnv;
  /** Invoked once the update is fully staged and (for the unsupervised path)
   *  the restart handoff is confirmed spawned. index.ts wires this to the
   *  same SIGTERM handler SIGINT/SIGTERM already install, so shutdown goes
   *  through exactly one proven code path regardless of trigger. Never
   *  called if anything upstream of it fails — see class doc comment. */
  requestShutdown: () => void;
  /** Delay before requestShutdown actually fires, so the tool.invoke response
   *  this capability returns has time to flush over the WS before the
   *  process exits (mirrors OpenClaw's short pre-exit pause for the same
   *  race — restart-handoff.ts / LAUNCHD_SUPERVISED_RESTART_EXIT_DELAY_MS in
   *  the OpenClaw tree). Configurable for tests; 0 disables the delay. */
  preShutdownDelayMs?: number;
  /** Injectable for tests. */
  fetchImpl?: GatewayFetchLike;
  tarExec?: ExecLike;
  spawnImpl?: Parameters<typeof spawnGatewayRestartHandoff>[0]["spawnImpl"];
  detectSupervisor?: (env: NodeJS.ProcessEnv) => GatewaySupervisorMode;
  layoutOverride?: GatewayReleaseLayout;
  parentExitTimeoutMs?: number;
  healthGraceMs?: number;
}

export class GatewaySelfUpdateRuntime {
  private readonly currentVersion: string;
  private readonly layout: GatewayReleaseLayout;
  private readonly env: NodeJS.ProcessEnv;

  constructor(private readonly options: GatewaySelfUpdateRuntimeOptions) {
    this.currentVersion = requireNonEmptyString(options.currentVersion, "currentVersion");
    this.env = options.env ?? process.env;
    this.layout =
      options.layoutOverride ??
      resolveGatewayReleaseLayout({ stateDir: options.stateDir, env: this.env });
  }

  requestedCapabilities(): string[] {
    return [GATEWAY_SELF_UPDATE_CAPABILITY];
  }

  supportsCapability(capabilityId: string): boolean {
    return String(capabilityId || "").trim() === GATEWAY_SELF_UPDATE_CAPABILITY;
  }

  async handleCapabilityInvoke(
    frame: GatewayRequestEnvelope<GatewayToolInvokePayload>,
  ): Promise<Record<string, unknown>> {
    const capabilityId = String(frame.payload?.capability_id || "").trim();
    if (capabilityId !== GATEWAY_SELF_UPDATE_CAPABILITY) {
      throw new Error(`Unsupported gateway self-update capability: ${capabilityId || "unknown"}`);
    }
    const args = frame.payload?.arguments ?? {};
    const targetVersion = requireNonEmptyString(args.target_version, "target_version");
    const artifactUrl = requireNonEmptyString(args.artifact_url, "artifact_url");
    const expectedSha256 = typeof args.sha256 === "string" ? args.sha256.trim() : undefined;

    if (targetVersion === this.currentVersion) {
      return {
        updated: false,
        reason: "already_current",
        current_version: this.currentVersion,
        target_version: targetVersion,
      };
    }

    const stagingDir = defaultDownloadStagingDir();
    // Both the download and the extract-into-a-release-dir step happen
    // against `stagingDir` before anything is renamed into the real
    // releasesDir path — the `finally` below cleans it up unconditionally,
    // so a failure at either step never leaks a temp tarball/dir.
    let releaseDir: string;
    let entrypoint: string;
    let checksumVerified: boolean;
    try {
      const download = await downloadGatewayArtifact({
        artifactUrl,
        destDir: stagingDir,
        expectedSha256,
        fetchImpl: this.options.fetchImpl,
      });
      checksumVerified = download.checksumVerified;
      const installed = await installGatewayArtifact({
        layout: this.layout,
        version: targetVersion,
        archivePath: download.archivePath,
        tarExec: this.options.tarExec,
      });
      releaseDir = installed.releaseDir;
      entrypoint = installed.entrypoint;
    } finally {
      await fsp.rm(stagingDir, { recursive: true, force: true }).catch(() => undefined);
    }

    const swap = await swapCurrentGatewaySymlink(this.layout, releaseDir);
    const previousEntry = await resolvePreviousEntrypoint(swap.previousTarget);

    const detectSupervisor = this.options.detectSupervisor ?? ((env) => detectGatewaySupervisor(env));
    const supervisorMode = detectSupervisor(this.env);

    if (supervisorMode === "systemd" || supervisorMode === "launchd") {
      // Restart=always (or the launchd equivalent, once one is configured)
      // relaunches through the same launcher, which re-resolves `current` —
      // already swapped above. Nothing else to do; just let shutdown happen.
      this.scheduleShutdown();
      return {
        updated: true,
        current_version: this.currentVersion,
        target_version: targetVersion,
        release_dir: releaseDir,
        restart_mode: "supervised",
        supervisor: supervisorMode,
        checksum_verified: checksumVerified,
      };
    }

    // Unsupervised: spawn the detached handoff BEFORE exiting. If this
    // throws or comes back without a pid, roll the symlink back and fail
    // the invoke with the OLD build still fully running — never exit into
    // a box nothing will relaunch.
    let handoff;
    try {
      handoff = await spawnGatewayRestartHandoff({
        layout: this.layout,
        parentPid: process.pid,
        newEntry: entrypoint,
        previousEntry,
        previousCurrentTarget: swap.previousTarget,
        logPath: path.join(this.options.stateDir, "self-update-handoff.log"),
        spawnImpl: this.options.spawnImpl,
        parentExitTimeoutMs: this.options.parentExitTimeoutMs,
        healthGraceMs: this.options.healthGraceMs,
      });
    } catch (error) {
      await rollbackCurrentGatewaySymlink(this.layout, swap.previousTarget).catch(() => undefined);
      throw new Error(
        `Gateway update was staged at ${releaseDir} but the restart handoff failed to start ` +
          `(${error instanceof Error ? error.message : String(error)}); rolled back to the previous build. ` +
          `This process is still running the old build (${this.currentVersion}).`,
      );
    }
    if (!handoff.pid) {
      await rollbackCurrentGatewaySymlink(this.layout, swap.previousTarget).catch(() => undefined);
      throw new Error(
        `Gateway update was staged at ${releaseDir} but the restart handoff process did not report a pid; ` +
          `rolled back to the previous build. This process is still running the old build (${this.currentVersion}).`,
      );
    }

    this.scheduleShutdown();
    return {
      updated: true,
      current_version: this.currentVersion,
      target_version: targetVersion,
      release_dir: releaseDir,
      restart_mode: "handoff",
      handoff_pid: handoff.pid,
      handoff_log_path: handoff.logPath,
      checksum_verified: checksumVerified,
    };
  }

  private scheduleShutdown(): void {
    const delayMs = this.options.preShutdownDelayMs ?? 1_000;
    if (delayMs <= 0) {
      this.options.requestShutdown();
      return;
    }
    const timer = setTimeout(() => this.options.requestShutdown(), delayMs);
    timer.unref?.();
  }
}

import path from "path";

import type { GatewayRequestEnvelope, GatewayToolInvokePayload } from "../protocol/types";
import {
  detectGatewaySupervisor,
  spawnGatewayRestartHandoff,
  type GatewaySupervisorMode,
} from "./gateway-restart-handoff";
import { resolveGatewayReleaseLayout, type GatewayReleaseLayout } from "./gateway-release-layout";
import { writePendingGatewayRestartMarker } from "./gateway-restart-pending";

/**
 * In-product "restart the gateway" — gap-hardware-gateway.md Part 1 item 2.
 * Today the only way to restart a stuck gateway process without destroying
 * the whole VPS is SSH + `systemctl restart` (the hardware detail page's own
 * guided copy for applying a new CLAUDE_CODE_OAUTH_TOKEN literally tells the
 * operator to do that — frontend/app/(account)/w/[workspaceId]/hardware/
 * [gatewayId]/page.tsx).
 *
 * This capability closes that gap using ONLY the existing, already-proven
 * restart-handoff machinery gateway.self_update already relies on for the
 * "the process needs to come back after this" half of an update
 * (gateway-restart-handoff.ts's clean-exit / detached-handoff code — see its
 * own module doc comment for the supervised-vs-unsupervised split, unchanged
 * here). The difference from self_update is everything BEFORE that: no
 * target_version/artifact_url argument, no download, no tar extraction, no
 * `current` symlink swap. This capability re-execs the exact build already
 * on disk — the same entrypoint this very process was launched from,
 * `require.main.filename` (same resolution gateway-doctor.ts's
 * defaultEntryPath() already uses) by default — so a restart works
 * identically whether or not self-update's release-layout has ever been
 * bootstrapped on this box (see gateway-self-update-runtime.ts's own
 * "BOOTSTRAP NOTE").
 *
 * Because nothing on disk changes, there is nothing to roll back TO if the
 * respawned process doesn't survive the handoff's health-grace window —
 * previousEntry/previousCurrentTarget are always null, so a failed restart
 * is reported in the handoff log and left alone rather than the script
 * re-launching the exact same (already-proven-unhealthy) entrypoint a
 * second time. Same crash-loop caution as self-update: the handoff script
 * itself is untouched (still the single, already-audited HANDOFF_SCRIPT),
 * and this class never calls process.exit directly — only ever through the
 * SAME requestShutdown() callback (SIGINT/SIGTERM's own clean-shutdown
 * path) self-update uses, and only after a spawn is confirmed (a pid came
 * back) or immediately for the supervised (Restart=always) case.
 *
 * Same post-restart health-check hand-off as self-update: writes a pending-
 * restart marker (gateway-restart-pending.ts) that the NEW process's boot
 * sequence (index.ts) picks up to run the in-gateway doctor once it
 * reconnects and report `health_check: "pass"|"fail"` back over
 * gateway.state.update — see that module's doc comment for why the answer
 * can't be known synchronously here either.
 */
export const GATEWAY_RESTART_CAPABILITY = "gateway.restart";

export interface GatewayRestartRuntimeOptions {
  /** The version this running process was built with — GATEWAY_VERSION in
   *  index.ts. Used only for the pending-restart marker (previousVersion ===
   *  targetVersion for a plain restart, nothing changed). */
  currentVersion: string;
  stateDir: string;
  env?: NodeJS.ProcessEnv;
  /** Same contract as GatewaySelfUpdateRuntimeOptions.requestShutdown: index.
   *  ts wires this to the SAME SIGINT/SIGTERM clean-shutdown path, never a
   *  second exit code path. */
  requestShutdown: () => void;
  preShutdownDelayMs?: number;
  /** The entrypoint to re-exec on the unsupervised (handoff) path — defaults
   *  to THIS process's own entrypoint. Injectable for tests. */
  currentEntrypoint?: string;
  spawnImpl?: Parameters<typeof spawnGatewayRestartHandoff>[0]["spawnImpl"];
  detectSupervisor?: (env: NodeJS.ProcessEnv) => GatewaySupervisorMode;
  parentExitTimeoutMs?: number;
  healthGraceMs?: number;
  layoutOverride?: GatewayReleaseLayout;
}

function defaultCurrentEntrypoint(): string {
  return require.main?.filename || process.argv[1] || process.execPath;
}

export class GatewayRestartRuntime {
  private readonly currentVersion: string;
  private readonly env: NodeJS.ProcessEnv;
  private readonly layout: GatewayReleaseLayout;

  constructor(private readonly options: GatewayRestartRuntimeOptions) {
    this.currentVersion = String(options.currentVersion || "").trim();
    this.env = options.env ?? process.env;
    // Only used for the handoff script's own informational logging (its
    // rollback branch is unreachable here — see class doc comment) and to
    // keep the pending-restart marker's stateDir-relative shape consistent
    // with gateway-self-update-runtime.ts. Never used to resolve/swap a
    // symlink from this class.
    this.layout =
      options.layoutOverride ??
      resolveGatewayReleaseLayout({ stateDir: options.stateDir, env: this.env });
  }

  requestedCapabilities(): string[] {
    return [GATEWAY_RESTART_CAPABILITY];
  }

  supportsCapability(capabilityId: string): boolean {
    return String(capabilityId || "").trim() === GATEWAY_RESTART_CAPABILITY;
  }

  async handleCapabilityInvoke(
    frame: GatewayRequestEnvelope<GatewayToolInvokePayload>,
  ): Promise<Record<string, unknown>> {
    const capabilityId = String(frame.payload?.capability_id || "").trim();
    if (capabilityId !== GATEWAY_RESTART_CAPABILITY) {
      throw new Error(`Unsupported gateway restart capability: ${capabilityId || "unknown"}`);
    }

    const detectSupervisor = this.options.detectSupervisor ?? ((env) => detectGatewaySupervisor(env));
    const supervisorMode = detectSupervisor(this.env);
    const entrypoint = this.options.currentEntrypoint ?? defaultCurrentEntrypoint();

    if (supervisorMode === "systemd" || supervisorMode === "launchd") {
      // Restart=always (or the launchd equivalent) relaunches through the
      // same launcher this process already runs under — nothing to spawn,
      // nothing on disk to change. Just let a clean exit happen.
      await writePendingGatewayRestartMarker(this.options.stateDir, {
        trigger: "restart",
        previousVersion: this.currentVersion,
        targetVersion: this.currentVersion,
        restartMode: "supervised",
        triggeredAt: new Date().toISOString(),
      });
      this.scheduleShutdown();
      return {
        restarted: true,
        restart_mode: "supervised",
        supervisor: supervisorMode,
        // See class doc comment — the true answer arrives later via
        // gateway.state.update once the new process reconnects.
        health_check: "pending",
      };
    }

    // Unsupervised: spawn the detached handoff BEFORE exiting — same
    // never-exit-into-a-void contract gateway.self_update uses. A
    // synchronous spawn failure (or no pid coming back) means this process
    // keeps running unchanged and the invoke fails, rather than exiting
    // into a box nothing will relaunch.
    let handoff;
    try {
      handoff = await spawnGatewayRestartHandoff({
        layout: this.layout,
        parentPid: process.pid,
        newEntry: entrypoint,
        // Nothing changed on disk, so there is no "previous" build to roll
        // back to — see class doc comment.
        previousEntry: null,
        previousCurrentTarget: null,
        logPath: path.join(this.options.stateDir, "restart-handoff.log"),
        spawnImpl: this.options.spawnImpl,
        parentExitTimeoutMs: this.options.parentExitTimeoutMs,
        healthGraceMs: this.options.healthGraceMs,
      });
    } catch (error) {
      throw new Error(
        `Gateway restart failed to start (${error instanceof Error ? error.message : String(error)}); ` +
          `this process is still running unchanged.`,
      );
    }
    if (!handoff.pid) {
      throw new Error(
        "Gateway restart handoff process did not report a pid; this process is still running unchanged.",
      );
    }

    await writePendingGatewayRestartMarker(this.options.stateDir, {
      trigger: "restart",
      previousVersion: this.currentVersion,
      targetVersion: this.currentVersion,
      restartMode: "handoff",
      triggeredAt: new Date().toISOString(),
    });
    this.scheduleShutdown();
    return {
      restarted: true,
      restart_mode: "handoff",
      handoff_pid: handoff.pid,
      handoff_log_path: handoff.logPath,
      health_check: "pending",
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

import { promises as fsp } from "fs";
import path from "path";

/**
 * Bridges a restart decision made in the OLD process (either gateway.
 * self_update swapping to a new build, or the plain gateway.restart
 * capability re-execing the same one) to the NEW process's startup.
 *
 * Why this has to exist at all: the capability-invoke response that decides
 * "restart now" has to be sent, and this process's shutdown scheduled,
 * BEFORE the new process even exists — the old process's cloud WS
 * connection is what carries that response, and it dies the moment this
 * process exits. That means the honest post-restart health check (did the
 * new process actually come up and reconnect, not just "did a PID exist for
 * 8 seconds" — see gateway-restart-handoff.ts's HANDOFF_SCRIPT) can only run
 * INSIDE the new process, after it boots, using the SAME in-gateway doctor
 * (health/gateway-doctor.ts) — and its result has to be reported back over
 * a fresh gateway.state.update call (see index.ts's afterConnected wiring),
 * not the original tool.invoke response.
 *
 * This marker is that hand-off: written by the OLD process right before it
 * schedules its own shutdown, read (and deleted — single-use, so a normal
 * reboot/crash-restart afterward is never mistaken for a fresh self-update
 * or restart) by the NEW process at startup so it knows whether to run that
 * post-restart doctor pass at all — a plain reboot with no marker present
 * runs none of this, zero added cost on the common path.
 */
export interface PendingGatewayRestartMarker {
  /** Which capability triggered the restart this marker describes. */
  trigger: "self_update" | "restart";
  previousVersion: string;
  /** Same as previousVersion for a plain gateway.restart (nothing changed);
   *  the build actually being swapped to for gateway.self_update. */
  targetVersion: string;
  restartMode: "supervised" | "handoff";
  triggeredAt: string;
}

function markerPath(stateDir: string): string {
  return path.join(stateDir, "gateway-restart-pending.json");
}

/** Best-effort only: a write failure here must never block or fail the
 *  restart/update itself — worst case the new process just boots without
 *  knowing to run a post-restart health check, which is a lesser signal,
 *  never a crash risk. */
export async function writePendingGatewayRestartMarker(
  stateDir: string,
  marker: PendingGatewayRestartMarker,
): Promise<void> {
  try {
    await fsp.mkdir(stateDir, { recursive: true });
    const tmpPath = `${markerPath(stateDir)}.tmp-${process.pid}`;
    await fsp.writeFile(tmpPath, `${JSON.stringify(marker, null, 2)}\n`, { mode: 0o600 });
    await fsp.rename(tmpPath, markerPath(stateDir));
  } catch {
    // Best effort — see doc comment above.
  }
}

/** Reads and deletes (single-use) the marker. Never throws: an absent or
 *  corrupt marker file both read as "nothing pending" — the safe default,
 *  since treating a normal boot as a post-update health-check moment would
 *  read a not-yet-connected process as a false "fail". */
export async function readAndClearPendingGatewayRestartMarker(
  stateDir: string,
): Promise<PendingGatewayRestartMarker | null> {
  const filePath = markerPath(stateDir);
  try {
    const raw = await fsp.readFile(filePath, "utf8");
    await fsp.rm(filePath, { force: true });
    return parsePendingGatewayRestartMarker(raw);
  } catch {
    return null;
  }
}

function parsePendingGatewayRestartMarker(raw: string): PendingGatewayRestartMarker | null {
  try {
    const parsed = JSON.parse(raw) as Partial<PendingGatewayRestartMarker> | null;
    if (!parsed || typeof parsed !== "object") {
      return null;
    }
    const trigger = parsed.trigger === "self_update" || parsed.trigger === "restart" ? parsed.trigger : null;
    const previousVersion = String(parsed.previousVersion ?? "").trim();
    const targetVersion = String(parsed.targetVersion ?? "").trim();
    const restartMode = parsed.restartMode === "supervised" || parsed.restartMode === "handoff" ? parsed.restartMode : null;
    const triggeredAt = String(parsed.triggeredAt ?? "").trim();
    if (!trigger || !previousVersion || !targetVersion || !restartMode || !triggeredAt) {
      return null;
    }
    return { trigger, previousVersion, targetVersion, restartMode, triggeredAt };
  } catch {
    return null;
  }
}

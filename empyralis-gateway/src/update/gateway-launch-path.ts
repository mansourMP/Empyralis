import fs from "fs";
import path from "path";

import { resolveGatewayReleaseLayout, type GatewayReleaseLayout } from "./gateway-release-layout";

/**
 * Which entrypoint a SUPERVISOR UNIT should be pinned to, so that a
 * self-update's `current` symlink swap actually takes effect on the next
 * start.
 *
 * This is the TypeScript half of a rule that already exists twice in shell —
 * `write_launcher_scripts()`'s run-gateway heredoc in
 * scripts/install-agent-computer.sh and its verbatim copy at
 * deploy/packer/files/run-gateway. Both check
 * `<installRoot>/current/gateway/dist/index.js` FIRST and fall back to the
 * installer-provisioned root-owned tree when it does not exist. Those two
 * launchers are therefore already correct, and this module deliberately
 * mirrors their ordering rather than inventing a second rule.
 *
 * It exists because the THIRD launcher — the macOS LaunchAgent plist written
 * by gateway-supervisor-install.ts — had no notion of the release layout at
 * all. Its entry path came from gateway-doctor.ts's `defaultEntryPath()`,
 * i.e. `require.main.filename`: THE PATH THIS PROCESS WAS ITSELF LAUNCHED
 * FROM. That is self-perpetuating — a gateway started once from a fixed
 * checkout writes that fixed checkout back into its own plist, and every
 * later self-update swaps a symlink the plist never resolves through. The
 * box then reports an unchanged build fingerprint forever
 * (gateway_build_identity_service's `previous_update_changed_nothing`), which
 * is detection, not repair.
 *
 * WHY THIS DOES NOT CHANGE THE OWNERSHIP MODEL: it only decides which of two
 * ALREADY-EXISTING paths a unit points at. It never writes into, chowns, or
 * assumes write access to the root-owned install tree
 * (/opt/empyralis/agent-computer), so gateway-release-layout.ts's constraint —
 * self-update stages releases next to the state dir precisely because the
 * unprivileged service user cannot write to the install tree — is preserved
 * exactly as written.
 *
 * BOOT-FAILURE POSTURE (this is a launcher path; getting it wrong strands a
 * machine nobody can SSH into): the layout entrypoint is returned ONLY when
 * it exists as a real, resolvable file at the moment the unit is written. A
 * missing or dangling `current` symlink falls back to the running process's
 * own entrypoint — the exact path that is, by construction, working right
 * now, since this process is running from it. A probe that throws (EACCES on
 * a parent dir, a symlink loop) is treated as "does not exist" and takes the
 * same fallback. So the worst case of this function is that a unit keeps the
 * path it already had, which is the pre-existing behaviour.
 */

/** The `dist/index.js` a release-layout `current` symlink resolves to.
 *  Mirrors run-gateway's first candidate exactly. */
export function currentReleaseEntrypoint(layout: GatewayReleaseLayout): string {
  return path.join(layout.currentSymlinkPath, "gateway", "dist", "index.js");
}

export interface ResolveGatewayLaunchEntrypointOptions {
  /** Where this process was actually launched from — the fallback, and the
   *  only path known to work at this instant. */
  runningEntryPath: string;
  /** Absent when the gateway has no resolved state dir, in which case there
   *  is no layout to prefer and the running path is the whole answer. */
  stateDir?: string;
  env?: NodeJS.ProcessEnv;
  /** Injectable for tests. Must never throw — a throwing probe is treated as
   *  "not usable" by resolveGatewayLaunchEntrypoint's own try/catch. */
  fileExists?: (filePath: string) => boolean;
}

function defaultFileExists(filePath: string): boolean {
  try {
    // statSync (not lstatSync) follows the `current` symlink deliberately: a
    // DANGLING symlink is exactly the case that must fall back, and lstat
    // would report it as present.
    return fs.statSync(filePath).isFile();
  } catch {
    return false;
  }
}

/**
 * Returns the entrypoint a supervisor unit should launch, preferring the
 * release layout's `current` build when it is actually resolvable.
 *
 * Never throws.
 */
export function resolveGatewayLaunchEntrypoint(
  opts: ResolveGatewayLaunchEntrypointOptions,
): string {
  const running = String(opts.runningEntryPath || "").trim();
  const stateDir = String(opts.stateDir || "").trim();
  if (!stateDir) {
    return running;
  }
  const fileExists = opts.fileExists ?? defaultFileExists;
  try {
    const layout = resolveGatewayReleaseLayout({ stateDir, env: opts.env });
    const candidate = currentReleaseEntrypoint(layout);
    if (candidate !== running && fileExists(candidate)) {
      return candidate;
    }
  } catch {
    // A layout that cannot be resolved is not a reason to fail a unit write.
  }
  return running;
}

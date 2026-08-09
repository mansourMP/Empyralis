/**
 * Acquiring the `openclaw` CLI itself, at the pin — the step that used to be
 * a human typing `npm i -g openclaw@2026.6.10`.
 *
 * WHY THIS EXISTS AT ALL
 * ----------------------
 * Provisioning's very first act is `checkOpenClawVersion`, and on a box that
 * has never had OpenClaw it refuses with `openclaw_not_installed`. That
 * refusal was correct and completely useless: nothing anywhere installed it,
 * so every freshly provisioned Empyralis box was permanently one manual
 * command away from having channels, and the customer — who must never learn
 * that OpenClaw exists — was the one being asked to type it.
 *
 * WHY IT LIVES HERE AND NOT ONLY IN THE INSTALLER SHELL SCRIPT
 * -----------------------------------------------------------
 * scripts/install-agent-computer.sh does run this too (it has root, and it
 * needs OpenClaw on disk before it can write a systemd unit whose ExecStart
 * points at the binary). But it gets the spec BY ASKING THE GATEWAY ARTIFACT
 * (`--print-install-plan` below), never by typing a version into bash. The
 * authoritative install is this one, for three reasons the shell cannot
 * cover:
 *
 *   1. ALREADY-PROVISIONED BOXES. A box installed before this change never
 *      re-runs the installer; it does take gateway self-updates. Putting the
 *      install on the gateway's own provisioning path is the only thing that
 *      reaches it.
 *   2. macOS / bring-your-own hardware. scripts/agent_computer.sh runs from a
 *      repo checkout on a developer's Mac, unprivileged, with no apt and no
 *      systemd. There is no root moment to hang a shell install off.
 *   3. THE PIN HAS ONE HOME. `OPENCLAW_PINNED_PACKAGE_SPEC` is a TypeScript
 *      constant beside the check that enforces it. A `npm i -g openclaw@X`
 *      literal in a shell script served over the public web — to boxes that
 *      fetched it months ago — is a second copy of a version number whose
 *      whole purpose is to be exact. CLAUDE.md has a whole section on a
 *      channel list copied into a third place; this is the same shape with a
 *      worse blast radius, because the drift is silent by construction (an
 *      off-pin CLI still runs, still answers, and quietly invalidates three
 *      transcribed contracts).
 *
 * IDEMPOTENCY IS GATED ON READ STATE, NEVER ON A TRY/CATCH
 * -------------------------------------------------------
 * Same rule ./openclaw-plugin-install.ts had to learn the hard way: their
 * `plugins install` re-downloads a tarball before failing on the second run,
 * so "just call it and catch" turns every reprovision into a slow,
 * network-dependent failure on a box that is already correct. `npm i -g` is
 * better behaved than that, but it still hits the registry every time — on a
 * boot-time reconcile, on a box with no network, that is a minutes-long stall
 * for a no-op. So the install is gated on `openclaw --version`: if the pinned
 * version is already there, this function shells out to NOTHING.
 *
 * FAILURE MUST NOT BRICK THE BOX
 * ------------------------------
 * A box that cannot reach npm still has to come up as a working Empyralis
 * gateway. This returns a structured outcome with a stable code; the
 * provisioner turns it into a `refused` result that reaches the cloud. It
 * never throws, and nothing above it treats a channel transport it could not
 * acquire as a reason to fail the gateway.
 */

import { execFileWithTimeout } from "../../shell/exec-file-with-timeout";
import type { OpenClawCli } from "./openclaw-cli";
import {
  OPENCLAW_PINNED_PACKAGE_SPEC,
  OPENCLAW_PINNED_VERSION,
  checkOpenClawVersion,
} from "./openclaw-version";

/** `npm i -g openclaw` on a cold cache took ~40s on the boxes this was
 *  measured on; a small droplet on a slow mirror is slower still. Generous,
 *  but bounded — an unbounded install is a wedged boot. */
const INSTALL_TIMEOUT_MS = 10 * 60_000;

export type OpenClawRuntimeInstallAction =
  /** Already at the pin. Nothing was executed. */
  | "already_installed"
  /** Installed by this run. */
  | "installed"
  /** Not installed, and this run was not allowed to install it. */
  | "skipped"
  /** Tried and failed. `refusal` says why. */
  | "failed";

export type OpenClawRuntimeInstallRefusalCode =
  /** npm itself is missing — this box cannot acquire OpenClaw at all. */
  | "openclaw_runtime_npm_unavailable"
  /** npm ran and failed (network, registry, EACCES on the global prefix). */
  | "openclaw_runtime_install_failed"
  /** npm reported success but the CLI is still absent or off the pin. */
  | "openclaw_runtime_install_unverified";

export interface OpenClawRuntimeInstallOutcome {
  action: OpenClawRuntimeInstallAction;
  /** What `openclaw --version` says after this function is done. */
  observedVersion?: string;
  expectedVersion: string;
  packageSpec: string;
  refusal?: { code: OpenClawRuntimeInstallRefusalCode; detail: string };
}

export type NpmRunner = (
  args: string[],
  timeoutMs: number,
) => Promise<{ code: number; stdout: string; stderr: string }>;

export interface EnsureOpenClawRuntimeOptions {
  cli: OpenClawCli;
  /** False turns this into a pure probe — used by callers that only want to
   *  report what is on the box. Default true. */
  install?: boolean;
  /** The npm binary. Resolved on PATH when unset. */
  npmPath?: string;
  /** Environment for the npm child. The caller passes the gateway's own
   *  process env, so NPM_CONFIG_PREFIX / NPM_CONFIG_CACHE / HOME — which
   *  scripts/install-agent-computer.sh already points at writable
   *  directories inside the service's ReadWritePaths for `cli.install` — are
   *  honoured and the global install lands somewhere the service user can
   *  actually write. */
  env?: NodeJS.ProcessEnv;
  /** Test seam. Replaces the child_process call entirely. */
  runNpm?: NpmRunner;
  record?: (messageType: string, payload: Record<string, unknown>) => Promise<unknown>;
}

function defaultNpmRunner(npmPath: string, env: NodeJS.ProcessEnv): NpmRunner {
  return async (args, timeoutMs) => {
    // execFileWithTimeout, never execFile's own `timeout` option — that one
    // fires a single SIGTERM, never escalates, and leaves the promise pending
    // forever against a child that ignores it (CLAUDE.md, and the drift
    // assertion in __tests__/exec-file-timeout-child-leak.test.ts).
    const result = await execFileWithTimeout(npmPath, args, timeoutMs, {
      env,
      maxBuffer: 8 * 1024 * 1024,
      encoding: "utf8" as const,
    });
    if (result.timedOut) {
      return { code: 124, stdout: result.stdout, stderr: `npm ${args.join(" ")} timed out after ${timeoutMs}ms.` };
    }
    if (result.error?.code === "ENOENT") {
      return { code: 127, stdout: "", stderr: `npm binary not found at "${npmPath}"` };
    }
    return { code: result.exitCode ?? 1, stdout: result.stdout, stderr: result.stderr };
  };
}

/**
 * Makes `openclaw` exist on this box at exactly the pinned version.
 *
 * Deliberately says nothing about configuration, lockdown or supervision —
 * those are ./openclaw-provisioner.ts's, and they run against whatever this
 * leaves behind. The ONLY postcondition claimed here is the one
 * `checkOpenClawVersion` will re-verify a moment later, from scratch, out of
 * the CLI's own mouth: this function's own idea of success is never the thing
 * provisioning trusts.
 */
export async function ensureOpenClawRuntimeInstalled(
  options: EnsureOpenClawRuntimeOptions,
): Promise<OpenClawRuntimeInstallOutcome> {
  const expectedVersion = OPENCLAW_PINNED_VERSION;
  const packageSpec = OPENCLAW_PINNED_PACKAGE_SPEC;
  const base = { expectedVersion, packageSpec };

  // ── Read state first. A correct box executes nothing below this. ───────
  const before = checkOpenClawVersion(await options.cli.version());
  if (before.ok) {
    return { ...base, action: "already_installed", observedVersion: before.observed };
  }

  if (options.install === false) {
    return { ...base, action: "skipped", observedVersion: before.observed };
  }

  const env = options.env ?? process.env;
  const npmPath = String(options.npmPath || "").trim() || "npm";
  const runNpm = options.runNpm ?? defaultNpmRunner(npmPath, env);

  await options.record?.("openclaw.runtime_install.started", {
    package_spec: packageSpec,
    // The reason we are installing: absent, unreadable, or the wrong version.
    // An off-pin CLI is REPLACED, not left alone — `npm i -g <name>@<exact>`
    // downgrades as happily as it upgrades, which is the point of a pin.
    reason: before.code ?? "openclaw_not_installed",
    observed_version: before.observed ?? null,
  });

  const install = await runNpm(["install", "--global", "--no-fund", "--no-audit", packageSpec], INSTALL_TIMEOUT_MS);
  if (install.code === 127) {
    return {
      ...base,
      action: "failed",
      observedVersion: before.observed,
      refusal: {
        code: "openclaw_runtime_npm_unavailable",
        detail:
          "This computer has no usable npm, so the channel transport cannot be installed. Channels will stay " +
          "unavailable; everything else on this computer is unaffected.",
      },
    };
  }
  if (install.code !== 0) {
    return {
      ...base,
      action: "failed",
      observedVersion: before.observed,
      refusal: {
        code: "openclaw_runtime_install_failed",
        detail:
          `Could not install the channel transport (${packageSpec}): ` +
          (install.stderr.trim() || install.stdout.trim() || `npm exited ${install.code}`).slice(0, 800) +
          " Channels will stay unavailable until this computer can reach the npm registry; everything else on it " +
          "is unaffected.",
      },
    };
  }

  // ── Verify out of the CLI's own mouth, not out of npm's exit code. ─────
  const after = checkOpenClawVersion(await options.cli.version());
  if (!after.ok) {
    return {
      ...base,
      action: "failed",
      observedVersion: after.observed,
      refusal: {
        code: "openclaw_runtime_install_unverified",
        detail:
          `npm reported success installing ${packageSpec}, but the installed CLI does not answer as ${expectedVersion} ` +
          `(${after.code ?? "unknown"}). Most often the global install landed outside this process's PATH. ` +
          (after.detail ?? ""),
      },
    };
  }

  await options.record?.("openclaw.runtime_install.applied", {
    package_spec: packageSpec,
    observed_version: after.observed ?? null,
  });
  return { ...base, action: "installed", observedVersion: after.observed };
}

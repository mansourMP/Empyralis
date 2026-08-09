/**
 * The seam between the root-time shell installer and everything this package
 * already knows about OpenClaw.
 *
 * `scripts/install-agent-computer.sh` has exactly two things the gateway
 * process does not: root, and a moment BEFORE the gateway has ever run. It
 * needs both, because a systemd unit lives in /etc/systemd/system (which an
 * unprivileged gateway cannot write) and because that unit's ExecStart must
 * point at a binary that already exists.
 *
 * What it must NOT have is an opinion. Every value below — the pinned package
 * spec, the unit path, the unit's exact bytes, the profile, the port, the
 * child environment — is computed here, by the same code the gateway itself
 * runs, from the same `loadGatewayConfig()` and the same renderers. The shell
 * script writes bytes it was handed and never composes them.
 *
 *   scripts/install-agent-computer.sh
 *     sudo -u empyralis node …/openclaw-install-plan-cli.js --ensure-runtime
 *          │                                    │
 *          │                                    ├─ ensureOpenClawRuntimeInstalled()
 *          │                                    │    (the pin lives in ONE file)
 *          │                                    ├─ resolveOpenClawLocalSecrets()
 *          │                                    │    (minted AS THE SERVICE USER,
 *          │                                    │     so the gateway reads the
 *          │                                    │     same file back later)
 *          │                                    └─ resolveExpectedOpenClawSupervisorUnit()
 *          │                                         (the ONE unit renderer)
 *          └─ writes {unit.path} ← {unit.contents}, daemon-reload, enable
 *
 * Why a JSON-printing child process instead of teaching bash the values:
 * because a version number, a unit body and an environment block transcribed
 * into a shell script served over the public web are three copies that drift
 * silently, on boxes that fetched that script months ago and will never fetch
 * it again. CLAUDE.md has the same lesson recorded twice already (a channel
 * list copied into a third place; a compiled artifact `grep` cannot see).
 *
 * RUN AS THE SERVICE USER, ALWAYS. This mints the loopback secrets and writes
 * them under the gateway's state dir; running it as root would leave a
 * root-owned file the gateway then cannot read, and the transport would be
 * dead in a way that looks like nothing happened. The installer's invocation
 * uses `sudo -u`, and `--require-user` makes a mistake here loud.
 */

import os from "os";
import path from "path";

import { loadGatewayConfig, openClawGatewayPortFromUrl } from "../../config";
import { OPENCLAW_INBOUND_PATH } from "../inbound-listener";
import { resolveOpenClawLocalSecrets } from "../openclaw-local-secrets";
import { OpenClawCli, openClawProfileStateDir } from "./openclaw-cli";
import { buildOpenClawProvisioningRuntime } from "./openclaw-provisioning-runtime";
import { ensureOpenClawRuntimeInstalled, type OpenClawRuntimeInstallOutcome } from "./openclaw-runtime-install";
import {
  buildOpenClawSupervisedEnv,
  resolveExpectedOpenClawSupervisorUnit,
} from "./openclaw-supervisor-unit";
import { OPENCLAW_PINNED_PACKAGE_SPEC, OPENCLAW_PINNED_VERSION } from "./openclaw-version";

export interface OpenClawInstallPlan {
  profile: string;
  pinnedVersion: string;
  packageSpec: string;
  /** Absolute path to the `openclaw` binary this plan's unit will exec.
   *  Resolved, never guessed — an ExecStart that is not absolute is a unit
   *  systemd refuses to load. */
  binaryPath?: string;
  gatewayPort: number;
  profileStateDir: string;
  /** null on a platform with neither systemd nor launchd, and on macOS —
   *  where the gateway installs its own LaunchAgent perfectly well without
   *  root, so the installer has nothing to do. */
  unit: { mode: string; name: string; path: string; contents: string } | null;
  runtimeInstall?: OpenClawRuntimeInstallOutcome;
  /** Present when --provision ran. Same shape the cloud sees. */
  provision?: { mode: string; status: string; refusal: unknown; lockdownViolations: unknown };
}

export interface BuildOpenClawInstallPlanOptions {
  /** Install the pinned CLI as part of building the plan. The installer wants
   *  this (it needs the binary to exist before the unit points at it); a
   *  caller only inspecting the box does not. */
  ensureRuntime?: boolean;
  /**
   * Run the baseline provision — write OpenClaw's locked-down config, run
   * their security audit, record the applied policy — before the caller
   * installs and STARTS a supervisor unit for it.
   *
   * The ordering is the whole reason this option exists. OpenClaw refuses to
   * start without `gateway.mode: "local"` in its config ("suspicious or
   * clobbered config"), so a unit started at install time on an unprovisioned
   * profile enters a 5-second restart loop and stays in it until something
   * else happens to write a config. Provisioning here means the first start
   * is the correct one; the gateway's own boot pass then finds a stored
   * record, takes the reconcile branch, and is a no-op.
   */
  provision?: boolean;
  /**
   * Install the pinned CLI and NOTHING else: no secrets resolved, no unit
   * rendered, no config written.
   *
   * This is what a pre-baked machine image needs (deploy/packer). The OpenClaw
   * package is identical on every box and is the slow, network-dependent part,
   * so baking it makes a first boot fast and offline-tolerant. The loopback
   * secrets are the opposite: baking those would ship ONE pair inside an image
   * every customer boots from, which is a shared credential across the fleet
   * dressed up as a per-box one. So the image gets the software and the box
   * gets its own secrets, and the two are separated by a flag rather than by
   * whoever writes the packer step remembering.
   */
  runtimeOnly?: boolean;
  env?: NodeJS.ProcessEnv;
  platform?: NodeJS.Platform;
  homeDir?: string;
}

/** Where `openclaw` ended up, asked of the shell rather than assumed. Returns
 *  undefined when it is not on PATH — the plan then carries no unit, because
 *  a unit whose ExecStart does not exist is a restart loop. */
async function resolveOpenClawBinaryPath(
  configured: string | undefined,
  env: NodeJS.ProcessEnv,
): Promise<string | undefined> {
  const explicit = String(configured || "").trim();
  if (explicit) return explicit;
  const { execFileWithTimeout } = await import("../../shell/exec-file-with-timeout");
  const result = await execFileWithTimeout("/usr/bin/env", ["sh", "-c", "command -v openclaw"], 15_000, {
    env,
    encoding: "utf8" as const,
  });
  const found = String(result.stdout || "").trim().split("\n")[0]?.trim();
  return found && path.isAbsolute(found) ? found : undefined;
}

export async function buildOpenClawInstallPlan(
  options: BuildOpenClawInstallPlanOptions = {},
): Promise<OpenClawInstallPlan> {
  const env = options.env ?? process.env;
  const platform = options.platform ?? process.platform;
  const homeDir = options.homeDir ?? os.homedir();
  const config = loadGatewayConfig(env);
  const profile = config.openclawProfile;
  const gatewayPort = openClawGatewayPortFromUrl(config.openclawGatewayUrl);

  // Minted here, as the service user, and read back by the gateway later from
  // the same file. This is also the moment a box that predates any of this
  // gets its secrets, since the installer is idempotent and re-runnable.
  //
  // SKIPPED ENTIRELY under runtimeOnly — see that option's doc comment: a
  // secret resolved while baking an image is a secret every box from that
  // image shares.
  const secrets = options.runtimeOnly
    ? { bridgeToken: "", gatewayToken: "", generated: [] as never[] }
    : await resolveOpenClawLocalSecrets({
        stateDir: config.stateDir,
        envBridgeToken: config.openclawBridgeToken,
        envGatewayToken: config.openclawGatewayToken,
      });

  let runtimeInstall: OpenClawRuntimeInstallOutcome | undefined;
  if (options.ensureRuntime) {
    runtimeInstall = await ensureOpenClawRuntimeInstalled({
      cli: new OpenClawCli({ profile, binaryPath: config.openclawBinaryPath, env }),
      env,
    });
  }

  const binaryPath = await resolveOpenClawBinaryPath(config.openclawBinaryPath, env);

  // Provision BEFORE the unit is written, through the same builder and the
  // same ensureProvisionedAtBoot() the gateway runs — never a second idea of
  // what provisioning means.
  let provision: OpenClawInstallPlan["provision"];
  if (options.provision && !options.runtimeOnly && binaryPath) {
    const runtime = buildOpenClawProvisioningRuntime({
      config,
      secrets,
      // dist/openclaw/provisioning/<this>.js -> dist/index.js, so
      // defaultBridgePluginPath resolves the package root exactly as it does
      // for the gateway process itself.
      entryPath: path.resolve(__dirname, "..", "..", "index.js"),
      env,
    });
    const outcome = await runtime.ensureProvisionedAtBoot();
    provision = {
      mode: outcome.mode,
      status: outcome.result.status,
      refusal: outcome.result.refusal ?? null,
      lockdownViolations: outcome.result.lockdownViolations,
    };
  }

  // macOS is deliberately unit-less here: `~/Library/LaunchAgents` needs no
  // privilege, so the gateway's own provisioning run installs and registers
  // the LaunchAgent itself. Handing the installer a plist to write would be a
  // second writer for a file with one owner.
  const unit =
    !options.runtimeOnly && platform === "linux" && binaryPath
      ? resolveExpectedOpenClawSupervisorUnit({
          profile,
          binaryPath,
          gatewayPort,
          environment: buildOpenClawSupervisedEnv({
            profile,
            homeDir,
            pathEnv: String(env.PATH || "/usr/local/bin:/usr/bin:/bin"),
            bridgeToken: secrets.bridgeToken,
            bridgeEndpointUrl: `http://127.0.0.1:${config.openclawBridgePort}${OPENCLAW_INBOUND_PATH}`,
          }),
          platform,
          homeDir,
          logDir: path.join(config.stateDir, "logs"),
        })
      : null;

  return {
    profile,
    pinnedVersion: OPENCLAW_PINNED_VERSION,
    packageSpec: OPENCLAW_PINNED_PACKAGE_SPEC,
    binaryPath,
    gatewayPort,
    profileStateDir: openClawProfileStateDir(profile, homeDir),
    unit: unit ? { mode: unit.mode, name: unit.name, path: unit.unitPath, contents: unit.contents } : null,
    runtimeInstall,
    provision,
  };
}

async function main(): Promise<void> {
  const argv = process.argv.slice(2);
  const requiredUser = (() => {
    const index = argv.indexOf("--require-user");
    return index >= 0 ? String(argv[index + 1] || "").trim() : "";
  })();
  if (requiredUser) {
    const actual = (() => {
      try {
        return os.userInfo().username;
      } catch {
        return "";
      }
    })();
    if (actual !== requiredUser) {
      // Loud, not best-effort. Minting the loopback secrets as the wrong user
      // leaves a file the gateway cannot read, and the failure downstream is a
      // channel transport that never comes up with nothing anywhere saying why.
      process.stderr.write(
        `openclaw-install-plan must run as "${requiredUser}", not "${actual || "unknown"}" — it writes the ` +
          "gateway's own state directory.\n",
      );
      process.exit(2);
      return;
    }
  }
  const plan = await buildOpenClawInstallPlan({
    ensureRuntime: argv.includes("--ensure-runtime") || argv.includes("--runtime-only"),
    provision: argv.includes("--provision"),
    runtimeOnly: argv.includes("--runtime-only"),
  });
  process.stdout.write(`${JSON.stringify(plan, null, 2)}\n`);
}

if (require.main === module) {
  main().catch((error: unknown) => {
    process.stderr.write(`${error instanceof Error ? error.stack || error.message : String(error)}\n`);
    process.exit(1);
  });
}

/**
 * Supervision for the co-located OpenClaw gateway — the SAME machinery that
 * supervises the Empyralis gateway itself, given a different definition.
 *
 * Everything here is a thin adapter: ../../update/gateway-supervisor-install.ts
 * owns the renderers, the drift audit, the conservative repair (write a
 * missing unit; rewrite a drifted one on disk only, never bootout/kickstart a
 * running job), and both OS registrars. This file supplies the OpenClaw
 * program definition and nothing else. There is exactly one supervisor
 * implementation in this codebase and this does not add a second.
 *
 * TWO THINGS ARE DIFFERENT ABOUT SUPERVISING OPENCLAW, AND BOTH MATTER:
 *
 *   1. The child's environment is fully specified, not inherited. launchd and
 *      systemd do not hand a login shell's environment to the jobs they
 *      start, so the unit file IS the environment. That is the strongest
 *      available guarantee that OpenClaw never receives a model provider
 *      credential: not "we set it empty", but "there is no such variable in
 *      the process". A key exported in the operator's ~/.zshrc cannot reach
 *      it. See openclaw-config-plan.ts's sanitizeOpenClawChildEnv for the
 *      other half (the CLI path).
 *
 *   2. The label is per-profile. One isolated instance per customer means one
 *      unit per customer; a shared label would have two customers' gateways
 *      fighting over one job.
 */

import os from "os";
import path from "path";

import {
  auditGatewaySupervisorUnit,
  createLaunchdJobRegistrar,
  createSystemdJobRegistrar,
  renderLaunchAgentPlist,
  renderSystemdUnit,
  repairGatewaySupervisorUnit,
  type GatewaySupervisorFileState,
  type GatewaySupervisorRepairResult,
  type GatewaySupervisorUnitDefinition,
} from "../../update/gateway-supervisor-install";
import { assertValidOpenClawProfile, openClawProfileStateDir } from "./openclaw-cli";

export interface OpenClawSupervisorUnitOptions {
  profile: string;
  /**
   * ABSOLUTE path to the `openclaw` binary, from
   * ./openclaw-binary-path.ts's resolveOpenClawBinaryPath — never a bare name
   * and never a configured value taken on faith.
   *
   * Deliberately `string | undefined` rather than optional: "we could not find
   * openclaw" is a state every caller must pass through explicitly, because
   * the wrong handling of it (writing a unit anyway) fails silently on both
   * platforms. See resolveExpectedOpenClawSupervisorUnit's refusal below.
   */
  binaryPath: string | undefined;
  gatewayPort: number;
  /** The FULL environment for the supervised process. Callers pass
   *  OpenClawCli.childEnv()'s sanitized copy, reduced to what OpenClaw
   *  actually needs — see buildOpenClawSupervisedEnv below. */
  environment: Record<string, string>;
  platform?: NodeJS.Platform;
  homeDir?: string;
  logDir: string;
  /**
   * systemd `User=` for the OpenClaw unit. Linux only; a launchd LaunchAgent
   * already runs as its own user.
   *
   * Defaults to the account THIS process runs as, resolved from the OS rather
   * than configured. Two things go wrong without it, and both are quiet: a
   * unit in /etc/systemd/system with no `User=` runs as ROOT, which is absurd
   * authority for a process whose whole job is to be a radio; and `--profile
   * <p>` resolves against `$HOME`, so a root-run OpenClaw keeps its state in
   * /root/.openclaw-<p> while the gateway reads and writes its own home —
   * two instances of one config, presenting as settings that keep reverting.
   */
  systemdUser?: string;
}

/** The account a systemd unit for THIS process's OpenClaw should run as.
 *  `os.userInfo()` reads the real uid's passwd entry, so it is right even
 *  under systemd's own `User=`, where `$USER` is typically unset. Only a
 *  genuinely unreadable passwd entry yields undefined. */
export function resolveDefaultOpenClawSystemdUser(): string | undefined {
  try {
    const name = String(os.userInfo().username || "").trim();
    return name.length > 0 ? name : undefined;
  } catch {
    return undefined;
  }
}

/** launchd label / systemd unit name for one customer's instance. */
export function openClawSupervisorLabel(profile: string): string {
  return `ai.empyralis.openclaw.${assertValidOpenClawProfile(profile)}`;
}

/**
 * The minimal environment OpenClaw is launched into. Deliberately an
 * allowlist, not a filtered copy of `process.env`: a denylist (see
 * OPENCLAW_FORBIDDEN_CREDENTIAL_ENV_PATTERNS) is right for the CLI path
 * because those calls must still work in whatever shell an operator has, but
 * for a long-lived supervised process the correct posture is "nothing except
 * what it needs".
 *
 * EMPYRALIS_BRIDGE_* is included because the bridge plugin runs INSIDE this
 * process and reads its intake endpoint/secret from the environment
 * (openclaw-bridge-plugin/src/config.ts). That secret is the low-privilege
 * loopback one, never a cloud credential.
 */
export function buildOpenClawSupervisedEnv(params: {
  profile: string;
  homeDir: string;
  pathEnv: string;
  bridgeToken: string;
  bridgeEndpointUrl: string;
}): Record<string, string> {
  return {
    HOME: params.homeDir,
    PATH: params.pathEnv,
    OPENCLAW_PROFILE: assertValidOpenClawProfile(params.profile),
    EMPYRALIS_BRIDGE_TOKEN: params.bridgeToken,
    EMPYRALIS_BRIDGE_ENDPOINT_URL: params.bridgeEndpointUrl,
    EMPYRALIS_BRIDGE_QUEUE_FILE: path.join(
      openClawProfileStateDir(params.profile, params.homeDir),
      "empyralis-bridge-queue.json",
    ),
  };
}

/**
 * The unit this platform should have for this customer's OpenClaw instance.
 *
 * Returns null in TWO cases, and the caller reports "unsupported" for both
 * rather than treating either as a failure (auditAndRepairOpenClawSupervisorUnit
 * below distinguishes them in `unsupportedReason`, because they are different
 * facts and this codebase does not collapse those):
 *
 *  1. A platform neither launchd nor systemd applies to, exactly as
 *     resolveExpectedSupervisorUnit does for the Empyralis gateway.
 *  2. A program path that is not ABSOLUTE — including the bare "openclaw"
 *     OpenClawCli quite correctly defaults to for its own execFile calls.
 *     Refusing here rather than rendering is the whole point: launchd
 *     resolves ProgramArguments[0] against its OWN minimal PATH and not the
 *     plist's EnvironmentVariables.PATH, so a bare name dies before exec with
 *     status 78 and an empty log file; systemd rejects a non-absolute
 *     ExecStart at unit load. Both failures are silent, permanent, and
 *     indistinguishable from "the channel transport is broken". A unit that
 *     provably cannot start is worse than no unit, because it also reads as
 *     supervision that exists. See ./openclaw-binary-path.ts.
 *
 * The argv is OpenClaw's own documented headless boot, verified live under an
 * isolated profile (CHANNEL-ADOPTION-PLAN.md's feasibility table). `--token`
 * is deliberately NOT passed: argv is world-readable via `ps`, and the token
 * is already in the config file this provisioning run wrote.
 */
export function resolveExpectedOpenClawSupervisorUnit(
  options: OpenClawSupervisorUnitOptions,
): GatewaySupervisorUnitDefinition | null {
  const platform = options.platform ?? process.platform;
  const homeDir = options.homeDir ?? os.homedir();
  const binaryPath = String(options.binaryPath || "").trim();
  if (!path.isAbsolute(binaryPath)) return null;
  const profile = assertValidOpenClawProfile(options.profile);
  const label = openClawSupervisorLabel(profile);
  const programArguments = [
    binaryPath,
    "--profile",
    profile,
    "gateway",
    "run",
    "--port",
    String(options.gatewayPort),
    "--bind",
    "loopback",
    "--auth",
    "token",
  ];
  const workingDirectory = openClawProfileStateDir(profile, homeDir);

  if (platform === "darwin") {
    const logPath = path.join(options.logDir, `openclaw-${profile}.log`);
    return {
      mode: "launchd",
      name: label,
      unitPath: path.join(homeDir, "Library", "LaunchAgents", `${label}.plist`),
      logPath,
      contents: renderLaunchAgentPlist({
        label,
        programArguments,
        workingDirectory,
        logPath,
        environment: options.environment,
      }),
    };
  }
  if (platform === "linux") {
    const unitName = `${label}.service`;
    return {
      mode: "systemd",
      name: unitName,
      unitPath: path.join("/etc/systemd/system", unitName),
      contents: renderSystemdUnit({
        label: unitName,
        programArguments,
        workingDirectory,
        logPath: path.join(options.logDir, `openclaw-${profile}.log`),
        environment: options.environment,
        description: `Empyralis-managed OpenClaw channel transport (${profile})`,
        user: options.systemdUser ?? resolveDefaultOpenClawSystemdUser(),
      }),
    };
  }
  return null;
}

export interface OpenClawSupervisorInstallOutcome {
  supported: boolean;
  definition: GatewaySupervisorUnitDefinition | null;
  fileState: GatewaySupervisorFileState | "not_applicable";
  repair?: GatewaySupervisorRepairResult;
  /**
   * WHY there is no unit, when there is none. Present only alongside
   * `supported: false`.
   *
   * "this OS has no supervisor we manage" and "openclaw is not installed on
   * this box" are different facts with different remedies — the first is
   * permanent and expected, the second is a real, fixable reason the channel
   * transport is not running. One boolean cannot say which, and saying only
   * "unsupported" for the second is how a missing binary reads as a platform
   * limitation nobody investigates.
   */
  unsupportedReason?: "platform" | "binary_path_not_absolute";
}

export interface AuditAndRepairOpenClawSupervisorOptions extends OpenClawSupervisorUnitOptions {
  readFile: (filePath: string) => Promise<string>;
  writeFile: (filePath: string, contents: string) => Promise<void>;
  mkdir: (dirPath: string) => Promise<void>;
  registerJob?: (definition: GatewaySupervisorUnitDefinition) => Promise<void>;
}

/** Audit + (optionally) repair, delegating to the shared implementation.
 *  Never throws; an unsupported platform or an unwritable unit path is
 *  reported in the outcome. */
export async function auditAndRepairOpenClawSupervisorUnit(
  options: AuditAndRepairOpenClawSupervisorOptions,
  attemptRepair: boolean,
): Promise<OpenClawSupervisorInstallOutcome> {
  const definition = resolveExpectedOpenClawSupervisorUnit(options);
  if (!definition) {
    const platform = options.platform ?? process.platform;
    const platformSupervised = platform === "darwin" || platform === "linux";
    return {
      supported: false,
      definition: null,
      fileState: "not_applicable",
      unsupportedReason: platformSupervised ? "binary_path_not_absolute" : "platform",
    };
  }
  const fileState = await auditGatewaySupervisorUnit({ definition, readFile: options.readFile });
  if (!attemptRepair) {
    return { supported: true, definition, fileState };
  }
  const repair = await repairGatewaySupervisorUnit({
    definition,
    fileState,
    mkdir: options.mkdir,
    writeFile: options.writeFile,
    registerJob: options.registerJob,
  });
  return { supported: true, definition, fileState, repair };
}

/** The platform registrar, same two the Empyralis gateway's own install
 *  uses. Exported so the provisioner does not re-derive the choice. */
export function defaultOpenClawJobRegistrar(
  platform: NodeJS.Platform,
): ((definition: GatewaySupervisorUnitDefinition) => Promise<void>) | undefined {
  if (platform === "darwin") {
    return createLaunchdJobRegistrar(typeof process.getuid === "function" ? process.getuid() : 0);
  }
  if (platform === "linux") return createSystemdJobRegistrar();
  return undefined;
}

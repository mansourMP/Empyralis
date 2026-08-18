import { promises as fsp } from "fs";
import os from "os";
import path from "path";

import { currentReleaseEntrypoint } from "./gateway-launch-path";
import { resolveGatewayReleaseLayout } from "./gateway-release-layout";
import { detectGatewaySupervisor, type GatewaySupervisorMode } from "./gateway-restart-handoff";

/**
 * CAN A SELF-UPDATE ON THIS BOX EVER TAKE EFFECT? — asked of the supervisor
 * unit that will actually start the next process, not of this process.
 *
 * gateway-launch-path.ts fixed the FORWARD half of MAN-355: a unit this
 * gateway writes itself now points at the release layout instead of at
 * `require.main.filename`. That half only ever reaches a box nothing
 * supervises yet — `SUPERVISOR_PRESENCE_CHECK.detect` (health/gateway-
 * doctor.ts) short-circuits to "pass" the moment detectGatewaySupervisor()
 * sees a systemd/launchd env hint, so `auditSupervisorInstall` is never
 * called on a box that is ALREADY supervised. An already-supervised box
 * carrying a mis-pinned unit therefore does not self-heal, and production is
 * exactly that box:
 *
 *     ExecStart=/usr/bin/node /opt/empyralis-app/empyralis-gateway/dist/index.js
 *                             ↑ outside the release layout entirely, so the
 *                               `current` symlink a self-update swaps is a
 *                               symlink the next start never resolves through
 *
 * AND IT CANNOT REPAIR ITSELF. Measured on production (165.227.25.201,
 * read-only) rather than assumed — three independent barriers, any ONE of
 * which is fatal to the idea of a gateway rewriting its own unit:
 *
 *     Uid: 995 (empyralis-gw)          /etc/systemd/system is root:root 0755
 *                                      → `test -w` as that user: NOT WRITABLE
 *     ProtectSystem=strict             / is mounted `ro` inside the unit's own
 *                                      mount namespace, so even root inside it
 *                                      cannot write there
 *     NoNewPrivileges=true             no setuid, no sudo, no escalation path
 *
 * plus `systemctl daemon-reload` itself needs root or a polkit rule the box
 * does not have. So this module deliberately does NOT attempt repair. It
 * answers one question honestly and reports it, because the alternative —
 * advertising an update to a box that structurally cannot receive one — is
 * the fleet-wide restart loop gateway_build_identity_service.py exists to
 * prevent, arriving one poll earlier.
 *
 * WHY THE ANSWER IS NOT "is my own entrypoint inside the layout". A freshly
 * installed box has never self-updated, so no `gateway-releases/current`
 * exists yet and it is running out of the installer's root-owned tree — an
 * entrypoint OUTSIDE the layout, on a box whose launcher is perfectly
 * correct and whose next update would work. Judging by the running path
 * would condemn most of the fleet. The launcher is the thing being judged.
 *
 * FAIL-SAFE DIRECTION, and it is the whole safety argument: every failure to
 * read, find, parse or classify anything resolves to `"unknown"`, never to
 * `"not_updatable"`. A wrong "unknown" costs a signal; a wrong
 * "not_updatable" takes updates away from a healthy box. Nothing here
 * writes, execs, or mutates a single byte.
 */

export type GatewayLaunchUpdatabilityStatus = "updatable" | "not_updatable" | "unknown";

/** Stable codes. Never matched on prose anywhere — this codebase has been
 *  bitten by string matching before (an error bucket matched "ai limit"
 *  through a reword). */
export const LAUNCH_CODE_UNIT_POINTS_INTO_LAYOUT = "unit_points_into_release_layout";
export const LAUNCH_CODE_LAUNCHER_RESOLVES_LAYOUT = "launcher_resolves_release_layout";
export const LAUNCH_CODE_PATH_OUTSIDE_LAYOUT = "launch_path_outside_release_layout";
export const LAUNCH_CODE_NO_RESTART_ON_CLEAN_EXIT = "supervisor_will_not_restart_on_clean_exit";

export interface GatewayLaunchBlocker {
  code: string;
  detail: string;
}

export interface GatewayLaunchUpdatability {
  status: GatewayLaunchUpdatabilityStatus;
  supervisor: GatewaySupervisorMode;
  /** The unit/plist that decides what starts next, when one was found. */
  unitPath: string | null;
  /** Exactly what that unit launches, verbatim, so an operator reading the
   *  Hardware page sees the same line they will edit. */
  launchCommand: string | null;
  /** What the unit would have to resolve through for a swap to take effect. */
  expectedEntrypoint: string | null;
  /** Every blocker found, each naming a DIFFERENT fact. Collapsing them
   *  would send an operator to fix one and leave the box still stuck. */
  blockers: GatewayLaunchBlocker[];
  /** Why the answer is "unknown" — set only for that status, so "we could
   *  not tell" is never read as "nothing is wrong". */
  unknownReason: string | null;
}

export interface ClassifyGatewayLaunchUpdatabilityOptions {
  env?: NodeJS.ProcessEnv;
  platform?: NodeJS.Platform;
  homeDir?: string;
  /** Where this process's state dir is; absent means no layout can be
   *  derived and the answer is "unknown". */
  stateDir?: string;
  /** Injectable IO. Both must reject/throw rather than lie; every throw is
   *  caught and becomes "unknown". */
  readFile?: (filePath: string) => Promise<string>;
  supervisorMode?: GatewaySupervisorMode;
}

const SYSTEMD_UNIT_SEARCH_DIRS = [
  "/etc/systemd/system",
  "/run/systemd/system",
  "/usr/local/lib/systemd/system",
  "/lib/systemd/system",
  "/usr/lib/systemd/system",
];

/** systemd restarts after a CLEAN exit only under these. Everything else —
 *  `on-failure`, `on-abnormal`, `on-abort`, `on-watchdog`, `no`, and the
 *  DEFAULT of no `Restart=` line at all — leaves the box down after the
 *  self-update runtime's supervised handoff exits 0. */
const SYSTEMD_RESTARTS_ON_CLEAN_EXIT = new Set(["always", "on-success"]);

/** A file only gets content-sniffed as a launcher when it declares itself a
 *  script. Keeps this from reading /usr/bin/node looking for a path. */
const MAX_LAUNCHER_BYTES = 256 * 1024;

async function defaultReadFile(filePath: string): Promise<string> {
  return fsp.readFile(filePath, "utf-8");
}

function unknown(
  reason: string,
  supervisor: GatewaySupervisorMode,
  partial: Partial<GatewayLaunchUpdatability> = {},
): GatewayLaunchUpdatability {
  return {
    status: "unknown",
    supervisor,
    unitPath: partial.unitPath ?? null,
    launchCommand: partial.launchCommand ?? null,
    expectedEntrypoint: partial.expectedEntrypoint ?? null,
    blockers: [],
    unknownReason: reason,
  };
}

/** True when `candidate` is the directory itself or sits underneath it.
 *  Plain path arithmetic, deliberately not realpath: the layout usually
 *  does not exist yet on a box that has never self-updated, and a realpath
 *  of a missing path answers nothing. */
export function isPathInside(parentDir: string, candidate: string): boolean {
  const parent = path.resolve(parentDir);
  const child = path.resolve(candidate);
  if (child === parent) {
    return true;
  }
  return child.startsWith(parent.endsWith(path.sep) ? parent : `${parent}${path.sep}`);
}

/** The unit name supervising THIS process.
 *
 *  `/proc/self/cgroup` is the discovery mechanism because the unit name is
 *  NOT in the environment — systemd sets INVOCATION_ID/JOURNAL_STREAM but
 *  never the unit's own name — and because production's unit is
 *  `empyralis-gateway-channels.service`, i.e. NOT the default name
 *  resolveExpectedSupervisorUnit() would guess. Guessing the name would read
 *  a unit this process does not run under and report on the wrong file. */
export function parseSystemdUnitFromCgroup(cgroupContents: string): string | null {
  const matches = String(cgroupContents || "").match(/[A-Za-z0-9@_.\\-]+\.service/g);
  if (!matches || matches.length === 0) {
    return null;
  }
  return matches[matches.length - 1];
}

/** First `ExecStart=` of a systemd unit, with systemd's own optional prefix
 *  characters stripped (`-` ignore-failure, `@` argv[0] override, `+`/`!`/
 *  `!!` privilege modifiers) and line continuations joined. */
export function parseSystemdExecStart(unitContents: string): string | null {
  const joined = String(unitContents || "").replace(/[ \t]*\\\r?\n[ \t]*/g, " ");
  for (const rawLine of joined.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line.toLowerCase().startsWith("execstart=")) {
      continue;
    }
    let value = line.slice("execstart=".length).trim();
    while (value && "-@+!".includes(value[0])) {
      value = value.slice(1).trim();
    }
    return value || null;
  }
  return null;
}

/** systemd's effective `Restart=` for this unit — `null` when the unit does
 *  not say, which is itself the answer (`Restart=no` is the default). */
export function parseSystemdRestartPolicy(unitContents: string): string | null {
  let found: string | null = null;
  for (const rawLine of String(unitContents || "").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (line.toLowerCase().startsWith("restart=")) {
      // Last one wins, matching systemd's own last-assignment-wins parsing.
      found = line.slice("restart=".length).trim().toLowerCase() || null;
    }
  }
  return found;
}

/** ProgramArguments of a LaunchAgent/LaunchDaemon plist. */
export function parseLaunchdProgramArguments(plistContents: string): string[] {
  const source = String(plistContents || "");
  const arrayMatch = source.match(
    /<key>\s*ProgramArguments\s*<\/key>\s*<array>([\s\S]*?)<\/array>/i,
  );
  if (!arrayMatch) {
    return [];
  }
  const values: string[] = [];
  const stringPattern = /<string>([\s\S]*?)<\/string>/gi;
  let match: RegExpExecArray | null;
  while ((match = stringPattern.exec(arrayMatch[1])) !== null) {
    values.push(match[1].trim());
  }
  return values;
}

/** launchd relaunches after a clean exit only on a plain `KeepAlive` true.
 *  A KeepAlive DICT is conditional (SuccessfulExit/Crashed/…), so it is not
 *  a promise that a deliberate exit comes back. */
export function parseLaunchdKeepAlive(plistContents: string): boolean {
  const match = String(plistContents || "").match(/<key>\s*KeepAlive\s*<\/key>\s*<(true|false)\s*\/>/i);
  return Boolean(match && match[1].toLowerCase() === "true");
}

async function readIfExists(
  readFile: (filePath: string) => Promise<string>,
  filePath: string,
): Promise<string | null> {
  try {
    return await readFile(filePath);
  } catch {
    return null;
  }
}

/** Does this launcher resolve the release layout at start time?
 *
 *  Matched on the two STRUCTURAL tokens a launcher must contain to work at
 *  all — the entrypoint path shape it has to test for, and the root it has
 *  to derive — never on comments or wording. This is the one check that can
 *  tell an installer-provisioned box (correct since 2026-07-21) from an
 *  older one whose run-gateway predates the block and is genuinely stuck;
 *  `gateway-launch-updatability-installer-drift.test.ts` holds it to the
 *  real installer's own heredoc rather than to a copy. */
export function launcherResolvesReleaseLayout(scriptContents: string): boolean {
  const source = String(scriptContents || "");
  if (!source.startsWith("#!")) {
    return false;
  }
  const resolvesCurrent = source.includes("/current/gateway/dist/index.js");
  const derivesRoot =
    source.includes("EMPYRALIS_GATEWAY_INSTALL_ROOT") || source.includes("gateway-releases");
  return resolvesCurrent && derivesRoot;
}

function commandTokens(command: string): string[] {
  return String(command || "")
    .split(/\s+/)
    .map((token) => token.replace(/^["']|["']$/g, ""))
    .filter((token) => token.length > 0);
}

async function classifyLaunchCommand(params: {
  command: string;
  tokens: string[];
  installRoot: string;
  readFile: (filePath: string) => Promise<string>;
}): Promise<GatewayLaunchBlocker | null> {
  for (const token of params.tokens) {
    if (token.startsWith("/") && isPathInside(params.installRoot, token)) {
      return null;
    }
  }
  const program = params.tokens[0];
  if (program && program.startsWith("/")) {
    const contents = await readIfExists(params.readFile, program);
    if (
      contents !== null &&
      contents.length <= MAX_LAUNCHER_BYTES &&
      launcherResolvesReleaseLayout(contents)
    ) {
      return null;
    }
  }
  return {
    code: LAUNCH_CODE_PATH_OUTSIDE_LAYOUT,
    detail:
      `This computer starts its gateway from ${params.command || "an unknown command"}, which is ` +
      `outside the folder updates are installed into (${params.installRoot}). An update would ` +
      "install correctly and then start the old copy again.",
  };
}

/**
 * Answer, for the supervisor unit that will start the NEXT gateway process,
 * whether a self-update on this box could ever take effect.
 *
 * Never throws. Never writes. Never execs.
 */
export async function classifyGatewayLaunchUpdatability(
  opts: ClassifyGatewayLaunchUpdatabilityOptions = {},
): Promise<GatewayLaunchUpdatability> {
  const env = opts.env ?? process.env;
  const platform = opts.platform ?? process.platform;
  const readFile = opts.readFile ?? defaultReadFile;
  const supervisor = opts.supervisorMode ?? detectGatewaySupervisor(env, platform);

  try {
    if (supervisor === "none") {
      // Nothing supervises this process, so nothing is pinned wrong yet —
      // and gateway-doctor.ts's supervisor_presence check is the path that
      // installs a unit here, already using resolveGatewayLaunchEntrypoint.
      return unknown("This computer is not managed by a service supervisor yet.", supervisor);
    }
    const stateDir = String(opts.stateDir || "").trim();
    if (!stateDir) {
      return unknown("This computer has no resolved state folder to derive the update layout from.", supervisor);
    }
    const layout = resolveGatewayReleaseLayout({ stateDir, env });
    const expectedEntrypoint = currentReleaseEntrypoint(layout);

    let unitPath: string | null = null;
    let unitContents: string | null = null;
    let launchCommand: string | null = null;
    const blockers: GatewayLaunchBlocker[] = [];

    if (supervisor === "systemd") {
      const configuredUnit = String(env.EMPYRALIS_SYSTEMD_UNIT || "").trim();
      const cgroup = await readIfExists(readFile, "/proc/self/cgroup");
      const unitName = configuredUnit || (cgroup ? parseSystemdUnitFromCgroup(cgroup) : null);
      if (!unitName) {
        return unknown("Could not work out which service definition starts this computer's gateway.", supervisor, {
          expectedEntrypoint,
        });
      }
      for (const dir of SYSTEMD_UNIT_SEARCH_DIRS) {
        const candidate = path.join(dir, unitName);
        const contents = await readIfExists(readFile, candidate);
        if (contents !== null) {
          unitPath = candidate;
          unitContents = contents;
          break;
        }
      }
      if (unitContents === null) {
        return unknown(`Could not read the service definition ${unitName} on this computer.`, supervisor, {
          expectedEntrypoint,
        });
      }
      launchCommand = parseSystemdExecStart(unitContents);
      if (!launchCommand) {
        return unknown(`The service definition at ${unitPath} does not say what it starts.`, supervisor, {
          unitPath,
          expectedEntrypoint,
        });
      }
      const restart = parseSystemdRestartPolicy(unitContents);
      if (!restart || !SYSTEMD_RESTARTS_ON_CLEAN_EXIT.has(restart)) {
        blockers.push({
          code: LAUNCH_CODE_NO_RESTART_ON_CLEAN_EXIT,
          detail:
            "This computer is set to bring the gateway back only when it crashes, so the clean " +
            "shutdown an update ends with would leave it switched off instead of starting the " +
            "new version.",
        });
      }
    } else {
      const label =
        String(env.EMPYRALIS_LAUNCHD_LABEL || "").trim() ||
        String(env.XPC_SERVICE_NAME || "").trim();
      if (!label || label === "0") {
        return unknown("Could not work out which login item starts this computer's gateway.", supervisor, {
          expectedEntrypoint,
        });
      }
      const homeDir = String(opts.homeDir || "").trim() || os.homedir();
      const candidates = [
        path.join(homeDir, "Library", "LaunchAgents", `${label}.plist`),
        path.join("/Library/LaunchDaemons", `${label}.plist`),
        path.join("/Library/LaunchAgents", `${label}.plist`),
      ];
      for (const candidate of candidates) {
        const contents = await readIfExists(readFile, candidate);
        if (contents !== null) {
          unitPath = candidate;
          unitContents = contents;
          break;
        }
      }
      if (unitContents === null) {
        return unknown(`Could not read the login item ${label} on this computer.`, supervisor, {
          expectedEntrypoint,
        });
      }
      const programArguments = parseLaunchdProgramArguments(unitContents);
      if (programArguments.length === 0) {
        return unknown(`The login item at ${unitPath} does not say what it starts.`, supervisor, {
          unitPath,
          expectedEntrypoint,
        });
      }
      launchCommand = programArguments.join(" ");
      if (!parseLaunchdKeepAlive(unitContents)) {
        blockers.push({
          code: LAUNCH_CODE_NO_RESTART_ON_CLEAN_EXIT,
          detail:
            "This computer is not set to bring the gateway back automatically after it stops, so " +
            "the clean shutdown an update ends with would leave it switched off instead of " +
            "starting the new version.",
        });
      }
    }

    const pathBlocker = await classifyLaunchCommand({
      command: launchCommand,
      tokens: commandTokens(launchCommand),
      installRoot: layout.installRoot,
      readFile,
    });
    if (pathBlocker) {
      blockers.push(pathBlocker);
    }

    return {
      status: blockers.length > 0 ? "not_updatable" : "updatable",
      supervisor,
      unitPath,
      launchCommand,
      expectedEntrypoint,
      blockers,
      unknownReason: null,
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return unknown(`Could not check how this computer starts its gateway — ${message}.`, supervisor);
  }
}

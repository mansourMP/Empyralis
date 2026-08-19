import { promises as fsp } from "fs";
import os from "os";
import path from "path";

import { execFileWithTimeout } from "../shell/exec-file-with-timeout";
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
 * THE UNIT FILE IS NOT THE CONFIGURATION, AND READING IT CONDEMNED THE ONE
 * BOX THAT HAD ALREADY BEEN REPAIRED. The first version of this module
 * parsed `/etc/systemd/system/<unit>` directly. systemd merges drop-ins
 * (`<unit>.d/*.conf`) over that file, and a drop-in is EXACTLY the repair
 * docs/DEPLOY-RUNBOOK.md tells an operator to apply — the only one that does
 * not mean editing a unit the installer owns. Measured on production minutes
 * after that repair landed:
 *
 *     systemctl show      ExecStart=/var/lib/empyralis-gw/state/launch/run-gateway
 *     (EFFECTIVE)         Restart=always                        ← REPAIRED
 *
 *     the base unit FILE  ExecStart=/usr/bin/node /opt/…/dist/index.js
 *     (what we read)      Restart=on-failure                    ← stale, forever
 *
 *     …service.d/empyralis-updatable.conf   exists, wins in systemd, unread
 *
 *     reported: not_updatable, 2 blockers        reality: fully updatable
 *
 * So a correctly repaired box reported itself permanently broken on the
 * Hardware page — telling every customer who followed our own written
 * instructions that the fix had not worked. That is worse than the bug it
 * was reporting. The effective configuration is therefore asked OF SYSTEMD
 * (`systemctl show`), the only source that includes drop-ins, and of launchd
 * (`launchctl print`), the only source that reflects the job as actually
 * LOADED rather than as last written to disk. The unit/plist FILE survives
 * only as a fallback, and a fallback answer is MARKED as one
 * (`configSource`) instead of being passed off as the effective truth.
 *
 * WHY THE ANSWER IS NOT "is my own entrypoint inside the layout". A freshly
 * installed box has never self-updated, so no `gateway-releases/current`
 * exists yet and it is running out of the installer's root-owned tree — an
 * entrypoint OUTSIDE the layout, on a box whose launcher is perfectly
 * correct and whose next update would work. Judging by the running path
 * would condemn most of the fleet. The launcher is the thing being judged.
 *
 * FAIL-SAFE DIRECTION, and it is the whole safety argument: every failure to
 * read, run, find, parse or classify anything resolves to `"unknown"`, never
 * to `"not_updatable"`. A wrong "unknown" costs a signal; a wrong
 * "not_updatable" takes updates away from a healthy box. That now extends to
 * the fallback itself: when systemd could not be asked and the base FILE
 * alone would produce blockers, the answer is `"unknown"` — because a
 * drop-in nobody could see is precisely the thing that would clear them.
 * Nothing here writes or mutates a single byte; the only commands it ever
 * runs are read-only queries of the supervisor's own state.
 */

export type GatewayLaunchUpdatabilityStatus = "updatable" | "not_updatable" | "unknown";

/** Which source the answer came from. A file-derived answer is never
 *  presented as if the supervisor had been asked — that conflation is the
 *  whole bug this field exists to make visible. */
export type GatewayLaunchConfigSource =
  | "systemd-effective"
  | "systemd-unit-file"
  | "launchd-effective"
  | "launchd-plist-file";

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
  /** Where `launchCommand` and the restart policy actually came from. Null
   *  when neither source could be reached at all. */
  configSource: GatewayLaunchConfigSource | null;
  /** Files layered over the base unit (systemd drop-ins). Empty on launchd,
   *  which has no equivalent mechanism. An operator looking at a
   *  base-file-vs-reality disagreement needs to be told these exist. */
  overridePaths: string[];
}

/** Shape of a read-only supervisor query. Deliberately not the raw
 *  `execFileWithTimeout` result: nothing here needs signals or kill
 *  semantics, and a narrow type keeps a test's stub honest. */
export interface GatewayLaunchQueryResult {
  exitCode: number | null;
  stdout: string;
  stderr: string;
  timedOut: boolean;
}

export type GatewayLaunchCommandRunner = (
  command: string,
  args: string[],
) => Promise<GatewayLaunchQueryResult>;

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
  /** Injectable read-only supervisor query. Must never reject — the default
   *  never does. Absent in a test means the file fallback is exercised. */
  runCommand?: GatewayLaunchCommandRunner;
  /** launchd's domain for a per-user agent is `gui/<uid>`; injectable so a
   *  test does not depend on the uid it happens to run as. */
  uid?: number | null;
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

/** Both queries are local, answered from the supervisor's own in-memory
 *  state, and used at connect time. A wedged one must never hold the boot
 *  path, so the deadline is the caller's, per shell/exec-file-with-timeout. */
const SUPERVISOR_QUERY_TIMEOUT_MS = 5_000;

async function defaultReadFile(filePath: string): Promise<string> {
  return fsp.readFile(filePath, "utf-8");
}

const defaultRunCommand: GatewayLaunchCommandRunner = async (command, args) => {
  const result = await execFileWithTimeout(command, args, SUPERVISOR_QUERY_TIMEOUT_MS);
  return {
    // A spawn failure (ENOENT: no systemctl on PATH) is a failure to ASK,
    // never an answer — surfaced as a non-zero code so the caller falls back.
    exitCode: result.error && typeof result.error.code === "string" ? null : result.exitCode,
    stdout: result.stdout,
    stderr: result.stderr,
    timedOut: result.timedOut,
  };
};

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
    configSource: partial.configSource ?? null,
    overridePaths: partial.overridePaths ?? [],
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

/** `systemctl show`'s output: one `Key=Value` per line, a key repeated once
 *  per value when it has several (a unit may declare several ExecStart=).
 *
 *  Values are kept verbatim — no trimming of the value side. systemd emits
 *  no leading whitespace, and trimming would quietly rewrite a path that
 *  legitimately ends in a space. */
export function parseSystemctlShowProperties(stdout: string): Map<string, string[]> {
  const properties = new Map<string, string[]>();
  for (const rawLine of String(stdout || "").split(/\r?\n/)) {
    const separator = rawLine.indexOf("=");
    if (separator <= 0) {
      continue;
    }
    const key = rawLine.slice(0, separator).trim();
    if (!key) {
      continue;
    }
    const value = rawLine.slice(separator + 1);
    const existing = properties.get(key);
    if (existing) {
      existing.push(value);
    } else {
      properties.set(key, [value]);
    }
  }
  return properties;
}

/**
 * One `ExecStart=` value as `systemctl show` renders it — a STRUCTURE, not
 * the line from the unit file:
 *
 *   ExecStart={ path=/x/run-gateway ; argv[]=/x/run-gateway ; ignore_errors=no ;
 *               start_time=[…] ; pid=2338249 ; code=(null) ; status=0/0 }
 *
 * `argv[]` is the command as it will actually be executed and is what an
 * operator recognises; `path` is the binary alone and is the fallback for a
 * shape that omits argv. Fields are ` ; `-separated, so the value is taken
 * up to the next separator rather than to the end of the line — reading to
 * the closing brace would drag systemd's whole runtime status (pid, exit
 * code) into the command an operator is told they are running.
 *
 * A value that is NOT in the structured form is returned as-is, so an older
 * systemd that prints a bare command still parses.
 */
export function parseSystemdExecStartProperty(value: string): string | null {
  const raw = String(value || "").trim();
  if (!raw) {
    return null;
  }
  if (!raw.startsWith("{")) {
    return raw;
  }
  const body = raw.replace(/^\{\s*/, "").replace(/\s*\}$/, "");
  const fields = body.split(/\s;\s/);
  let programPath: string | null = null;
  for (const field of fields) {
    const trimmed = field.trim();
    if (trimmed.startsWith("argv[]=")) {
      const argv = trimmed.slice("argv[]=".length).trim();
      if (argv) {
        return argv;
      }
    }
    if (trimmed.startsWith("path=")) {
      const candidate = trimmed.slice("path=".length).trim();
      if (candidate) {
        programPath = candidate;
      }
    }
  }
  return programPath;
}

/** First `ExecStart=` of a systemd unit FILE, with systemd's own optional
 *  prefix characters stripped (`-` ignore-failure, `@` argv[0] override,
 *  `+`/`!`/`!!` privilege modifiers) and line continuations joined.
 *
 *  Only the fallback path uses this. The effective answer comes from
 *  `systemctl show`, because this function cannot see a drop-in. */
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

/** systemd's `Restart=` as written in a unit FILE — `null` when the file
 *  does not say, which is itself the answer (`Restart=no` is the default).
 *  Same drop-in blindness as the function above; fallback only. */
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

/**
 * `launchctl print`'s command, which is the job AS LOADED:
 *
 *   program = /usr/local/bin/node
 *   arguments = {
 *     /usr/local/bin/node
 *     /Users/x/gateway/dist/index.js
 *   }
 *
 * `arguments` wins when present because a plist's ProgramArguments is what
 * a gateway plist actually carries; `program` alone is the shape launchd
 * reports for a job declared with `Program` and no argument vector.
 */
export function parseLaunchctlPrintCommand(printOutput: string): string | null {
  const source = String(printOutput || "");
  const argumentsMatch = source.match(/^[ \t]*arguments[ \t]*=[ \t]*\{([\s\S]*?)^[ \t]*\}/m);
  if (argumentsMatch) {
    const values = argumentsMatch[1]
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line.length > 0);
    if (values.length > 0) {
      return values.join(" ");
    }
  }
  const programMatch = source.match(/^[ \t]*program[ \t]*=[ \t]*(.+)$/m);
  if (programMatch) {
    const program = programMatch[1].trim();
    if (program) {
      return program;
    }
  }
  return null;
}

/** The plist `launchctl` says this job was loaded FROM, so an operator is
 *  pointed at the file that is actually in force rather than at whichever
 *  of the three search locations happened to exist. */
export function parseLaunchctlPrintPlistPath(printOutput: string): string | null {
  const match = String(printOutput || "").match(/^[ \t]*path[ \t]*=[ \t]*(.+)$/m);
  const value = match ? match[1].trim() : "";
  return value || null;
}

/**
 * Whether the LOADED job carries KeepAlive, read off `launchctl print`'s
 * `properties = a | b | c` line.
 *
 * POSITIVE-ONLY, deliberately. This token's presence is not documented by
 * Apple and could not be verified against a KeepAlive job without loading
 * one onto a machine this work is not allowed to modify — so its ABSENCE is
 * treated as "said nothing", and the plist file still decides. A parser
 * whose false negative manufactures a blocker would be condemning healthy
 * boxes on a guess, which is the exact direction this module forbids.
 */
export function parseLaunchctlPrintKeepAlive(printOutput: string): boolean {
  const match = String(printOutput || "").match(/^[ \t]*properties[ \t]*=[ \t]*(.+)$/m);
  if (!match) {
    return false;
  }
  return match[1]
    .split("|")
    .map((token) => token.trim().toLowerCase())
    .includes("keepalive");
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

interface EffectiveSystemdConfig {
  launchCommand: string;
  restart: string | null;
  fragmentPath: string | null;
  overridePaths: string[];
}

/**
 * Ask systemd — not the filesystem — what this unit runs.
 *
 * Returns null on every way of not getting an answer, which the caller
 * turns into the marked file fallback. `systemctl show` EXITS 0 FOR A UNIT
 * IT HAS NEVER HEARD OF (measured on production, systemd 255: it printed
 * `Restart=no` and no ExecStart line at all), so a missing ExecStart — not
 * the exit code — is what distinguishes "no such unit" from an answer. That
 * matters: taking the printed `Restart=no` at face value would invent a
 * restart blocker for a unit that does not exist.
 */
async function querySystemdEffectiveConfig(params: {
  run: GatewayLaunchCommandRunner;
  unitName: string;
}): Promise<EffectiveSystemdConfig | null> {
  let result: GatewayLaunchQueryResult;
  try {
    result = await params.run("systemctl", [
      "show",
      params.unitName,
      "--property=ExecStart",
      "--property=Restart",
      "--property=FragmentPath",
      "--property=DropInPaths",
      "--no-pager",
    ]);
  } catch {
    return null;
  }
  if (result.timedOut || result.exitCode !== 0) {
    return null;
  }
  const properties = parseSystemctlShowProperties(result.stdout);
  const execStartValues = properties.get("ExecStart") ?? [];
  let launchCommand: string | null = null;
  for (const value of execStartValues) {
    launchCommand = parseSystemdExecStartProperty(value);
    if (launchCommand) {
      break;
    }
  }
  if (!launchCommand) {
    return null;
  }
  const restartValues = properties.get("Restart") ?? [];
  const restart = (restartValues[restartValues.length - 1] || "").trim().toLowerCase() || null;
  const fragmentPath = (properties.get("FragmentPath")?.[0] || "").trim() || null;
  const overridePaths = (properties.get("DropInPaths")?.[0] || "")
    .trim()
    .split(/\s+/)
    .filter((entry) => entry.length > 0);
  return { launchCommand, restart, fragmentPath, overridePaths };
}

interface EffectiveLaunchdConfig {
  launchCommand: string;
  keepAlive: boolean;
  plistPath: string | null;
}

/**
 * Ask launchd what the LOADED job runs.
 *
 * launchd has no drop-in mechanism, so this is not the same bug systemd
 * had — but it is the same CLASS. launchd holds the job as it was
 * bootstrapped; editing the plist on disk changes nothing until the job is
 * booted out and back in. A plist edited to point somewhere worse, on a
 * still-loaded job, would make the file say "stuck" about a launcher that
 * is fine — the same false condemnation, arriving through staleness instead
 * of through layering. So the loaded job decides, and the file is the
 * fallback.
 */
async function queryLaunchdEffectiveConfig(params: {
  run: GatewayLaunchCommandRunner;
  label: string;
  uid: number | null;
}): Promise<EffectiveLaunchdConfig | null> {
  const domains: string[] = [];
  if (params.uid !== null && Number.isFinite(params.uid)) {
    domains.push(`gui/${params.uid}/${params.label}`);
  }
  domains.push(`system/${params.label}`);
  for (const domain of domains) {
    let result: GatewayLaunchQueryResult;
    try {
      result = await params.run("launchctl", ["print", domain]);
    } catch {
      continue;
    }
    if (result.timedOut || result.exitCode !== 0) {
      continue;
    }
    const launchCommand = parseLaunchctlPrintCommand(result.stdout);
    if (!launchCommand) {
      continue;
    }
    return {
      launchCommand,
      keepAlive: parseLaunchctlPrintKeepAlive(result.stdout),
      plistPath: parseLaunchctlPrintPlistPath(result.stdout),
    };
  }
  return null;
}

/**
 * Answer, for the supervisor unit that will start the NEXT gateway process,
 * whether a self-update on this box could ever take effect.
 *
 * Never throws. Never writes. The only subprocesses it starts are
 * `systemctl show` and `launchctl print`, both read-only queries of the
 * supervisor's own state, both under the caller's own deadline.
 */
export async function classifyGatewayLaunchUpdatability(
  opts: ClassifyGatewayLaunchUpdatabilityOptions = {},
): Promise<GatewayLaunchUpdatability> {
  const env = opts.env ?? process.env;
  const platform = opts.platform ?? process.platform;
  const readFile = opts.readFile ?? defaultReadFile;
  const runCommand = opts.runCommand ?? defaultRunCommand;
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
    let launchCommand: string | null = null;
    let configSource: GatewayLaunchConfigSource | null = null;
    let overridePaths: string[] = [];
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

      const effective = await querySystemdEffectiveConfig({ run: runCommand, unitName });
      let restart: string | null = null;
      if (effective) {
        configSource = "systemd-effective";
        launchCommand = effective.launchCommand;
        restart = effective.restart;
        overridePaths = effective.overridePaths;
        unitPath = effective.fragmentPath;
      }

      if (!effective) {
        // systemd could not be asked. The base file is all that is left, and
        // it is exactly the source that cannot see the documented repair.
        configSource = "systemd-unit-file";
        let unitContents: string | null = null;
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
            configSource,
          });
        }
        launchCommand = parseSystemdExecStart(unitContents);
        restart = parseSystemdRestartPolicy(unitContents);
      }

      if (!launchCommand) {
        return unknown(
          `The service definition${unitPath ? ` at ${unitPath}` : ""} does not say what it starts.`,
          supervisor,
          { unitPath, expectedEntrypoint, configSource, overridePaths },
        );
      }
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
      const uid =
        opts.uid !== undefined
          ? opts.uid
          : typeof process.getuid === "function"
            ? process.getuid()
            : null;

      const effective = await queryLaunchdEffectiveConfig({ run: runCommand, label, uid });
      let keepAlive = false;
      if (effective) {
        configSource = "launchd-effective";
        launchCommand = effective.launchCommand;
        unitPath = effective.plistPath;
        // Positive-only union: launchctl's own token when it says so, and
        // otherwise the plist launchd itself named as this job's source.
        // See parseLaunchctlPrintKeepAlive for why absence decides nothing.
        keepAlive = effective.keepAlive;
      }

      const candidates = [
        ...(effective?.plistPath ? [effective.plistPath] : []),
        path.join(homeDir, "Library", "LaunchAgents", `${label}.plist`),
        path.join("/Library/LaunchDaemons", `${label}.plist`),
        path.join("/Library/LaunchAgents", `${label}.plist`),
      ];

      if (effective && !keepAlive) {
        // The plist launchd itself named, then the standard locations. Any
        // one of them saying KeepAlive is enough, because the launchctl
        // token is positive-only and its silence means nothing.
        for (const candidate of candidates) {
          const plist = await readIfExists(readFile, candidate);
          if (plist !== null) {
            keepAlive = parseLaunchdKeepAlive(plist);
            break;
          }
        }
      }

      if (!effective) {
        configSource = "launchd-plist-file";
        let plistContents: string | null = null;
        for (const candidate of candidates) {
          const contents = await readIfExists(readFile, candidate);
          if (contents !== null) {
            unitPath = candidate;
            plistContents = contents;
            break;
          }
        }
        if (plistContents === null) {
          return unknown(`Could not read the login item ${label} on this computer.`, supervisor, {
            expectedEntrypoint,
            configSource,
          });
        }
        const programArguments = parseLaunchdProgramArguments(plistContents);
        launchCommand = programArguments.length > 0 ? programArguments.join(" ") : null;
        keepAlive = parseLaunchdKeepAlive(plistContents);
      }

      if (!launchCommand) {
        return unknown(
          `The login item${unitPath ? ` at ${unitPath}` : ""} does not say what it starts.`,
          supervisor,
          { unitPath, expectedEntrypoint, configSource },
        );
      }
      if (!keepAlive) {
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

    // THE ONE PLACE A FALLBACK ANSWER IS DOWNGRADED. A base unit file cannot
    // see the drop-in that is our own documented repair, so blockers derived
    // from it alone are exactly the false condemnation this module exists to
    // stop shipping. "Could not confirm" is the honest answer; only an
    // explicit "not_updatable" ever takes an update away from a box.
    //
    // The launchd fallback is deliberately NOT downgraded the same way:
    // launchd has no layering mechanism, so nothing can be sitting on top of
    // a plist silently correcting it. Its file answer can only be stale, and
    // a stale plist that reads as broken is a plist someone edited to be
    // broken — a fact worth reporting, not an invisible repair.
    if (configSource === "systemd-unit-file" && blockers.length > 0) {
      return unknown(
        "Could not ask this computer's service manager what it actually runs, and its base " +
          "service file alone cannot show later corrections, so this could not be confirmed " +
          "either way.",
        supervisor,
        { unitPath, launchCommand, expectedEntrypoint, configSource, overridePaths },
      );
    }

    return {
      status: blockers.length > 0 ? "not_updatable" : "updatable",
      supervisor,
      unitPath,
      launchCommand,
      expectedEntrypoint,
      blockers,
      unknownReason: null,
      configSource,
      overridePaths,
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return unknown(`Could not check how this computer starts its gateway — ${message}.`, supervisor);
  }
}

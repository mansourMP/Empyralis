/**
 * Where `openclaw` actually is on this box — ABSOLUTE, or nothing.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * WHY THIS IS ITS OWN MODULE, AND WHY THE ANSWER MAY BE "NOTHING"
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * A supervisor unit's program path is resolved by the SUPERVISOR, not by a
 * shell, and neither supervisor will look at the unit's own PATH to do it:
 *
 *   launchd   ProgramArguments[0] is resolved against launchd's own minimal
 *             PATH (/usr/bin:/bin:/usr/sbin:/sbin) — NOT against the plist's
 *             EnvironmentVariables.PATH. A bare "openclaw" therefore fails
 *             BEFORE exec, so the job dies with status 78 (EX_CONFIG) and
 *             writes NOTHING: launchd never got far enough to have a process
 *             whose stdout could be redirected. `launchctl list` shows the 78
 *             and the log file is zero bytes, forever.
 *   systemd   stricter still: a non-absolute `ExecStart=` fails unit
 *             validation at load. The unit never runs at all.
 *
 * Observed live on macOS 2026-08-14: the Empyralis-managed OpenClaw plist had
 * `ProgramArguments[0] == "openclaw"`, `launchctl list` reported `- 78
 * ai.empyralis.openclaw.empyralis` indefinitely, and running the identical
 * argv by hand in a normal shell worked perfectly — which is what proves the
 * failure is resolution and not the program.
 *
 * The reason a bare name ever got written is that the CLI path and the UNIT
 * path have different requirements and looked identical in code.
 * `OpenClawCli` defaults `binaryPath` to the bare string "openclaw" and is
 * completely right to: it runs through `execFile`, which DOES perform a PATH
 * lookup against the env it is handed. Handing that same default to a unit
 * renderer produces a file that provably cannot start. So the resolution is
 * pulled out here, once, and both callers that render a unit use it — never a
 * second resolver, and never a bare-name default that reads as one.
 *
 * `undefined` is a real, expected answer (openclaw not installed yet). A
 * caller that gets it must render NO unit — a unit whose program does not
 * exist is a five-second restart loop on systemd and a silent exit-78 corpse
 * on launchd, and both look exactly like "the transport just doesn't work".
 */

import path from "path";

/** Injectable PATH lookup, so a test never shells out. */
export type OpenClawBinaryLookup = (
  name: string,
  env: NodeJS.ProcessEnv,
) => Promise<string | undefined>;

/** `command -v`, run through the caller's env so the transport's own Node
 *  and npm-global bin dir are on the PATH being searched. The name is passed
 *  as a positional PARAMETER rather than interpolated into the script, so a
 *  configured value can never become shell syntax. */
const lookupOnPath: OpenClawBinaryLookup = async (name, env) => {
  const { execFileWithTimeout } = await import("../../shell/exec-file-with-timeout");
  const result = await execFileWithTimeout(
    "/usr/bin/env",
    ["sh", "-c", 'command -v "$1"', "sh", name],
    15_000,
    { env, encoding: "utf8" as const },
  );
  const found = String(result.stdout || "").trim().split("\n")[0]?.trim();
  return found || undefined;
};

/**
 * The absolute path to `openclaw`, or undefined when there is not one.
 *
 * `configured` is EMPYRALIS_OPENCLAW_BINARY (src/config.ts's
 * `openclawBinaryPath`), which is unset on essentially every box. An absolute
 * configured value is taken as given — an operator who names a path owns it.
 * A configured value that is NOT absolute is treated as a NAME to look up
 * rather than as an answer, because that is the only reading under which it
 * could ever have worked, and an unresolvable one yields undefined instead of
 * a unit that cannot start.
 */
export async function resolveOpenClawBinaryPath(
  configured: string | undefined,
  env: NodeJS.ProcessEnv,
  lookup: OpenClawBinaryLookup = lookupOnPath,
): Promise<string | undefined> {
  const explicit = String(configured || "").trim();
  if (explicit && path.isAbsolute(explicit)) return explicit;
  let found: string | undefined;
  try {
    found = await lookup(explicit || "openclaw", env);
  } catch {
    // A failed lookup is "not installed", never a thrown provisioning run:
    // the caller's correct response to both is the same (render no unit).
    return undefined;
  }
  return found && path.isAbsolute(found) ? found : undefined;
}

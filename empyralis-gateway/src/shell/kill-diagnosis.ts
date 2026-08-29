/**
 * WHY A COMMAND STOPPED, WHEN IT NEVER GOT TO SAY SO ITSELF.
 *
 * Measured on main before this existed, driving the real runOnHost shape:
 *
 * ```
 *   a command killed by the system    exit_code=null  stderr=""  timed_out=false
 *   an ordinary failure               exit_code=1     stderr="boom"
 * ```
 *
 * `null` and an empty stderr is the whole of what an agent was told. Node's
 * close handler was HANDED `signal: "SIGKILL"` and the second argument was
 * never read — the one fact that explains the row was thrown away one line
 * from where it arrived. In the container the same event surfaces as exit
 * 137 (128 + SIGKILL), a number with no explanation attached, which the model
 * then has to guess the meaning of in front of a person.
 *
 * A real annual ledger is squarely in the size range that triggers this: a
 * 512 MB container limit and a pandas read of a few hundred megabytes of CSV
 * meet at exactly this failure, and it is the FIRST thing a bookkeeping agent
 * will hit. "It failed" and "it was stopped before it could finish" are two
 * different facts, and the second one has an action attached to it.
 *
 * ── WHAT IS CERTAIN, AND WHAT IS ONLY LIKELY ─────────────────────────────
 * 137 is not proof of running out of memory. It is 128 + 9, i.e. SIGKILL,
 * which a command can also earn by running `kill -9` on itself. So the two
 * reasons below are graded by the evidence actually in hand:
 *
 * ```
 *   OUR OWN TIMEOUT      already reported as timed_out. Never diagnosed here —
 *                        checked FIRST, because we sent that signal ourselves
 *                        and calling it a memory problem would be a fabricated
 *                        diagnosis of our own deadline.
 *
 *   sandbox + a limit    memory_limit_exceeded. Inside a container we capped
 *   we set ourselves     with --memory, the only thing that SIGKILLs the main
 *                        process unprompted is the kernel reclaiming it. The
 *                        statement names the limit, so the claim is checkable
 *                        rather than asserted.
 *
 *   everything else      killed_by_the_system. On the host we set no limit, so
 *   (host, or no limit)  there is no number to name and no limit to blame. The
 *                        statement says the usual cause without claiming it.
 * ```
 *
 * The one thing that would make the sandbox case CERTAIN is the daemon's own
 * `State.OOMKilled` flag, and it is not reachable: every container runs
 * `--rm`, so it is gone before `docker inspect` could be asked. Dropping
 * `--rm` to win that race would trade a certain diagnosis for leaked
 * containers on every timeout, which is a worse bargain than a diagnosis that
 * states its own evidence. Do not add a best-effort inspect here: it would
 * lose the race essentially every time, which is a branch that reads as
 * confirmation and never confirms anything.
 *
 * ── THE STATEMENT SAYS WHAT WOULD HELP ───────────────────────────────────
 * Naming the shape of the fix — work through the file in pieces, or query it
 * where it sits instead of loading all of it — and never a library, an
 * apt package or a flag. The bookkeeping procedures already name the specific
 * tool; a tool result that also named one would go stale the day that changes,
 * and mechanism in customer-facing prose is the thing this product's own law
 * rules out.
 */

/** Why a command stopped without reporting for itself. Two values, because
 *  there are two genuinely different states of evidence — see the header. */
export type KillReason = "memory_limit_exceeded" | "killed_by_the_system";

export interface KillDiagnosis {
  reason: KillReason;
  /** One plain statement for whoever reads the tool result — the fact, then
   *  what would help. Never a warning, never an instruction to install
   *  anything. */
  statement: string;
  /** The limit that was actually in force, in MB, when we are the ones who
   *  set it. ABSENT on the host, where we set none — reporting a limit we did
   *  not impose would be inventing the number the whole diagnosis rests on. */
  memoryLimitMb?: number;
}

export interface KillDiagnosisInput {
  /** The process exit code. `null` when it died on a signal (host path). */
  exitCode: number | null;
  /** The signal that killed it, when the platform told us. Absent on the
   *  container path, where `docker run` reports 137 as an exit code instead. */
  signal?: NodeJS.Signals | string | null;
  /** Whether OUR deadline fired. When true this returns null — that kill is
   *  ours and is already reported. */
  timedOut: boolean;
  /** Which side of the container boundary this ran on. */
  isolation: "sandbox" | "host";
  /** The memory cap in force, when we set one. */
  memoryLimitMb?: number;
}

/** 128 + SIGKILL(9). What a shell reports for a child the kernel killed, and
 *  what `docker run` reports for a container the cgroup limit killed. */
export const SIGKILL_EXIT_CODE = 137;

const CHUNKING_ADVICE =
  "Working through the file in pieces, or querying it where it sits instead of loading all of it at once, needs far less memory.";

/**
 * The whole rule, pure, so the executor and its tests ask the same code the
 * same question.
 *
 * Returns null when nothing needs explaining: an ordinary exit (success or
 * failure — the command spoke for itself), or our own timeout, which is
 * already its own reported fact.
 */
export function diagnoseKill(input: KillDiagnosisInput): KillDiagnosis | null {
  // OURS FIRST, ALWAYS. We send SIGKILL on our own deadline, so every
  // signal-shaped signal below is ambiguous until this is excluded — and a
  // timeout diagnosed as a memory problem sends somebody to shrink a file
  // that was never too big.
  if (input.timedOut) return null;

  const killedBySignal = String(input.signal || "").toUpperCase() === "SIGKILL";
  const killedByExitCode = input.exitCode === SIGKILL_EXIT_CODE;
  if (!killedBySignal && !killedByExitCode) return null;

  const limit = input.memoryLimitMb;
  if (input.isolation === "sandbox" && typeof limit === "number" && limit > 0) {
    return {
      reason: "memory_limit_exceeded",
      memoryLimitMb: limit,
      statement:
        `This command was stopped before it finished because it needed more memory than it is allowed here — the limit is ${limit} MB. ` +
        `Anything it printed before stopping is real; the rest never ran. ` +
        CHUNKING_ADVICE,
    };
  }

  return {
    reason: "killed_by_the_system",
    statement:
      "This command was stopped by the computer before it finished, and it did not get the chance to say why. " +
      "Anything it printed before stopping is real; the rest never ran. " +
      "The usual cause is needing more memory than was free. " +
      CHUNKING_ADVICE,
  };
}

/** The fields a diagnosis contributes to a tool result, or nothing at all.
 *
 *  Spread into the result rather than nested, and ABSENT rather than null
 *  when there is nothing to say: an ordinary failure must stay byte-for-byte
 *  the shape it has always been, or every consumer of a normal `exit_code: 1`
 *  result learns a new key that is null on almost every call. */
export function killDiagnosisFields(
  diagnosis: KillDiagnosis | null,
): Record<string, unknown> {
  if (!diagnosis) return {};
  return {
    killed: true,
    kill_reason: diagnosis.reason,
    kill_statement: diagnosis.statement,
    ...(typeof diagnosis.memoryLimitMb === "number"
      ? { memory_limit_mb: diagnosis.memoryLimitMb }
      : {}),
  };
}

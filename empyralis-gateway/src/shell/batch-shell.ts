/**
 * shell.execute batching: let one dispatch carry several commands, executed
 * SEQUENTIALLY inside one shared container/host process, with all results
 * returned together in one round trip.
 *
 * Why this exists: an agent tool call that runs on a Gateway box costs a
 * full network round trip (cloud -> box -> cloud). Measured on a real box
 * far from the server: ~8s per call, of which ~7.7s is the round trip and
 * ~0.3s is the actual work on the machine (see runtime.ts's header for the
 * full breakdown). A model that needs N commands today pays N round trips.
 * This lets it pay one.
 *
 * DELIBERATE ISOLATION-SEMANTICS CHANGE, stated explicitly because it is
 * easy to miss: commands within ONE batch share a single shell process (and,
 * in sandbox mode, a single container) — so `cd`, `export`, and shell
 * functions set by command 1 are visible to command 2, exactly like typing
 * both lines into the same terminal. Separate batches (and separate single
 * shell.execute calls) stay fully isolated from each other, exactly as
 * today — every batch gets its own fresh container in sandbox mode.
 *
 * That sharing is what makes an ordinary sequence ("cd into the repo, then
 * run the build") work naturally instead of losing state between commands
 * the way today's one-container-per-call design does. It has one real
 * trade-off, and it is inherent to POSIX shell semantics, not a shortcut
 * taken here: a statement that mutates the shell's own state (`cd`,
 * `export`) cannot ALSO be run in a way that lets an external timer kill
 * just that one statement without killing the shell it's running in —
 * killing one always means killing the process, and forking a subprocess to
 * make it independently killable would lose the very state-sharing this
 * exists to provide. So a per-command `timeout_seconds` is a real budget
 * that is enforced by ending the WHOLE BATCH if the cumulative deadline
 * elapses while that command is still running — not by killing only that
 * one command and continuing on to the next. See buildBatchDriverScript's
 * own comment for exactly how each command still gets separated stdout,
 * stderr, and an exit code despite sharing one process.
 */

export const DEFAULT_BATCH_COMMAND_TIMEOUT_SECONDS = 60;
export const MAX_BATCH_COMMAND_TIMEOUT_SECONDS = 300;
export const MAX_BATCH_COMMANDS = 20;
export const MAX_BATCH_TOTAL_TIMEOUT_SECONDS = 900; // 15 minutes, hard ceiling regardless of per-command budgets
import { diagnoseKill } from "./kill-diagnosis";

const BATCH_TIMEOUT_OVERHEAD_BASE_SECONDS = 5; // container/shell startup
const BATCH_TIMEOUT_OVERHEAD_PER_COMMAND_SECONDS = 2; // per-command file I/O + process bookkeeping

export interface BatchCommandSpec {
  index: number;
  command: string;
  timeoutSeconds: number;
}

export interface ParsedBatchCommands {
  specs: BatchCommandSpec[];
  stopOnFailure: boolean;
}

function positiveIntOr(value: unknown, fallback: number, max: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return fallback;
  }
  return Math.min(Math.floor(parsed), max);
}

/**
 * Validates and normalizes the `commands` argument of a shell.execute
 * batch call. Throws (never returns a partial/best-effort result) on any
 * malformed entry — the caller is expected to refuse the WHOLE batch on a
 * parse failure, matching the "gate everything before executing anything"
 * rule for policy violations below.
 */
export function parseBatchCommandsArgument(
  raw: unknown,
  opts: { defaultTimeoutSeconds?: number; maxTimeoutSeconds?: number; maxCommands?: number } = {},
): BatchCommandSpec[] {
  const defaultTimeoutSeconds = opts.defaultTimeoutSeconds ?? DEFAULT_BATCH_COMMAND_TIMEOUT_SECONDS;
  const maxTimeoutSeconds = opts.maxTimeoutSeconds ?? MAX_BATCH_COMMAND_TIMEOUT_SECONDS;
  const maxCommands = opts.maxCommands ?? MAX_BATCH_COMMANDS;
  if (!Array.isArray(raw)) {
    throw new Error("commands must be an array of command strings (or {command, timeout_seconds} objects).");
  }
  if (raw.length === 0) {
    throw new Error("commands must not be empty.");
  }
  if (raw.length > maxCommands) {
    throw new Error(`commands has ${raw.length} entries, which exceeds the maximum batch size of ${maxCommands}.`);
  }
  return raw.map((item, index) => {
    let commandText: string;
    let rawTimeout: unknown;
    if (typeof item === "string") {
      commandText = item;
    } else if (item && typeof item === "object" && !Array.isArray(item)) {
      const obj = item as Record<string, unknown>;
      commandText = String(obj.command ?? "");
      rawTimeout = obj.timeout_seconds;
    } else {
      throw new Error(`commands[${index}] must be a string or an object with a "command" field.`);
    }
    commandText = commandText.trim();
    if (!commandText) {
      throw new Error(`commands[${index}] is empty.`);
    }
    return {
      index,
      command: commandText,
      timeoutSeconds: positiveIntOr(rawTimeout, defaultTimeoutSeconds, maxTimeoutSeconds),
    };
  });
}

/**
 * The whole batch's outer deadline: the sum of every command's own budget
 * (so a batch of slow commands gets proportionally more time), plus a fixed
 * overhead for container/shell startup and a small per-command allowance
 * for file I/O — capped at MAX_BATCH_TOTAL_TIMEOUT_SECONDS regardless of how
 * large the sum gets, so one call can never hold a box's Docker daemon (or,
 * in full_access mode, the box itself) for longer than that no matter what
 * per-command timeouts a caller requests.
 */
export function computeBatchTimeoutSeconds(specs: BatchCommandSpec[]): number {
  const sum = specs.reduce((total, spec) => total + spec.timeoutSeconds, 0);
  const overhead = BATCH_TIMEOUT_OVERHEAD_BASE_SECONDS + specs.length * BATCH_TIMEOUT_OVERHEAD_PER_COMMAND_SECONDS;
  return Math.min(sum + overhead, MAX_BATCH_TOTAL_TIMEOUT_SECONDS);
}

/** Minimal POSIX single-quote escaping. Every path this is applied to is
 *  built from a uuid hex string plus fixed literal segments — never from
 *  caller-supplied text — so this is defense-in-depth, not load-bearing. */
function shQuote(value: string): string {
  return `'${value.replace(/'/g, "'\\''")}'`;
}

/**
 * Builds the POSIX shell driver script that runs an entire batch as ONE
 * shell process. Each command's own text is written by the caller to its
 * own file (`cmd_<i>.sh`, alongside this script, under `batchDirPath`) —
 * never interpolated into this generated script — so nothing about a
 * command's content needs escaping here and no command's own syntax (an
 * unbalanced quote or brace) can corrupt the driver's control flow.
 *
 * Per command, in order:
 *   1. If an earlier command already tripped STOP (see below), this command
 *      is marked "skipped" and never runs.
 *   2. Otherwise: write "started" (so a command that begins but never
 *      finishes — because the whole batch's outer timeout fired while it
 *      was running — is distinguishable on read-back from one that never
 *      started at all).
 *   3. Run it with `{ . "cmd_i.sh"; } > out_i 2> err_i`. `{ ...; }` is a
 *      shell GROUP command, not a subshell (`( ... )`) — it does not fork.
 *      Combined with `.` (dot-source, which also does not fork), the
 *      command runs IN the driver's own process: `cd`/`export`/functions it
 *      sets are visible to every command after it, exactly like typing both
 *      into one terminal. The `{ }` still lets stdout/stderr be redirected
 *      for just that command's duration.
 *   4. Write its exit code and mark it "done".
 *   5. If stopOnFailure and the exit code was non-zero, set STOP so every
 *      later command is skipped rather than run. When stopOnFailure is
 *      false, STOP is never set — every command runs regardless of earlier
 *      failures.
 *
 * A command that calls `exit` ends the WHOLE script there (it is running in
 * the driver's own process, not a subshell) — intentional, and the same
 * thing a person would get typing `exit` in a real shell session; commands
 * strictly after it simply never get a status file written, which the
 * reader (interpretBatchResults) reports as "not run".
 */
export function buildBatchDriverScript(
  specs: BatchCommandSpec[],
  opts: { stopOnFailure: boolean; batchDirPath: string },
): string {
  const dir = opts.batchDirPath;
  const lines: string[] = ["#!/bin/sh", "set +e", `mkdir -p ${shQuote(dir)}`, "STOP=0"];
  for (const spec of specs) {
    const i = spec.index;
    const statusPath = shQuote(`${dir}/status_${i}`);
    const cmdPath = shQuote(`${dir}/cmd_${i}.sh`);
    const outPath = shQuote(`${dir}/out_${i}`);
    const errPath = shQuote(`${dir}/err_${i}`);
    const codePath = shQuote(`${dir}/code_${i}`);
    lines.push(
      `if [ "$STOP" = "0" ]; then`,
      `  echo started > ${statusPath}`,
      `  { . ${cmdPath}; } > ${outPath} 2> ${errPath}`,
      `  code=$?`,
      `  echo "$code" > ${codePath}`,
      `  echo done > ${statusPath}`,
    );
    if (opts.stopOnFailure) {
      lines.push(`  if [ "$code" != "0" ]; then STOP=1; fi`);
    }
    lines.push(`else`, `  echo skipped > ${statusPath}`, `fi`);
  }
  return lines.join("\n") + "\n";
}

/** "killed" is its own status and not a flavour of "failed": a command the
 *  system stopped never reached a result, so reporting it as a failure sends
 *  somebody to debug a command that was working. See kill-diagnosis.ts. */
export type BatchCommandStatus = "success" | "failed" | "killed" | "skipped" | "timed_out" | "not_run";

export interface BatchCommandResult {
  index: number;
  command: string;
  status: BatchCommandStatus;
  ran: boolean;
  exit_code: number | null;
  stdout: string;
  stderr: string;
  reason: string | null;
}

/** What was actually read back off disk for one command's status/output
 *  files after the batch finished (or was killed). `status` is the literal
 *  driver-script marker: "" (file never written), "started", "done", or
 *  "skipped". */
export interface RawCommandFileState {
  status: "" | "started" | "done" | "skipped";
  exitCode: number | null;
  stdout: string;
  stderr: string;
}

/**
 * Turns the raw per-command file state (what the driver script actually
 * left on disk) into the honest, per-command outcome CLAUDE.md's
 * outcome-honesty law requires: "failed", "did not run", and "succeeded"
 * are three different facts and must never share one representation. This
 * is driven entirely by which files exist, which is correct whether the
 * driver finished cleanly, was killed mid-command by the outer timeout, or
 * (stopOnFailure) chose to skip a command after an earlier one failed —
 * the file state alone distinguishes all of it, with no timing/clock
 * correlation needed.
 */
export function interpretBatchResults(
  specs: BatchCommandSpec[],
  states: Map<number, RawCommandFileState>,
  opts: {
    stopOnFailure: boolean;
    batchTimedOut: boolean;
    isolation?: "sandbox" | "host";
    memoryLimitMb?: number;
  },
): { results: BatchCommandResult[]; stoppedEarly: boolean } {
  let stoppedEarly = false;
  const results = specs.map((spec): BatchCommandResult => {
    const state = states.get(spec.index) ?? { status: "", exitCode: null, stdout: "", stderr: "" };
    if (state.status === "done") {
      const exitCode = state.exitCode;
      // "done" means the driver recorded a $? for this command — including
      // 137, which is what the shell reports for a child the kernel killed.
      // A killed command is not a failed one: it never got to run, let alone
      // fail, and "failed" with an empty stderr sends somebody looking for a
      // bug in a command that was working. The driver only ever sees a
      // numeric $?, never a signal name, so 137 is the whole signal here.
      const killed = diagnoseKill({
        exitCode,
        timedOut: false,
        isolation: opts.isolation ?? "host",
        memoryLimitMb: opts.memoryLimitMb,
      });
      return {
        index: spec.index,
        command: spec.command,
        status: killed ? "killed" : exitCode === 0 ? "success" : "failed",
        ran: true,
        exit_code: exitCode,
        stdout: state.stdout,
        stderr: state.stderr,
        reason: killed ? killed.statement : null,
      };
    }
    if (state.status === "skipped") {
      stoppedEarly = true;
      return {
        index: spec.index,
        command: spec.command,
        status: "skipped",
        ran: false,
        exit_code: null,
        stdout: "",
        stderr: "",
        reason: "not run: an earlier command in this batch failed and stop_on_failure was set (default).",
      };
    }
    if (state.status === "started") {
      // Wrote "started" but never "done": the batch's shared time budget
      // (or, non-deterministically, the container/host process itself)
      // ended while this command was still running. Whatever partial
      // stdout/stderr made it to disk before that is still real and is
      // returned — the exit code is genuinely unknown, never guessed.
      stoppedEarly = true;
      return {
        index: spec.index,
        command: spec.command,
        status: "timed_out",
        ran: true,
        exit_code: null,
        stdout: state.stdout,
        stderr: state.stderr,
        reason:
          "this command started but the batch's shared time budget ran out before it finished — its exit code is unknown; " +
          "any stdout/stderr shown is whatever it had written before the batch was stopped.",
      };
    }
    // No status file at all: the driver script never reached this command —
    // either the outer timeout killed the batch before this iteration ran,
    // or an earlier command's `exit` ended the script.
    stoppedEarly = true;
    return {
      index: spec.index,
      command: spec.command,
      status: "not_run",
      ran: false,
      exit_code: null,
      stdout: "",
      stderr: "",
      reason: opts.batchTimedOut
        ? "not run: the batch's shared time budget ran out before this command could start."
        : "not run: the batch ended (an earlier command's own `exit`, or a batch-level failure) before this command could start.",
    };
  });
  return { results, stoppedEarly };
}

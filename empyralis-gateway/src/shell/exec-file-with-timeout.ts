import { execFile, type ExecFileOptions } from "child_process";

/**
 * `child_process.execFile`'s own `timeout` option is NOT a timeout — it is a
 * single SIGTERM with no escalation and no promise contract.
 *
 * When the deadline elapses Node sends `killSignal` (SIGTERM by default) once
 * and then does nothing else, ever. The `execFile` CALLBACK still only fires
 * on the child's 'close'. So for any child that does not die on SIGTERM, the
 * callback never runs, the wrapping promise never settles, and the child's
 * ProcessWrap plus its three stdio PipeWraps stay refcounted on the event loop
 * for the lifetime of the process.
 *
 * That is not hypothetical. `docker info` on macOS ignores SIGTERM while it is
 * waiting on an unresponsive Docker Desktop socket: measured here, the child is
 * still alive minutes after `child.killed === true`, and it dies only on
 * SIGKILL. The long-running gateway probes Docker from
 * `health/service-inventory.ts` on boot, on every `shell.execute`, and from the
 * heartbeat, so each wedged probe left one immortal `docker` child behind. A
 * gateway that had been up three days was found holding four of them, with
 * fifty more reparented to init from earlier gateway processes — the oldest
 * over a day old.
 *
 * This helper makes the timeout mean what callers already believe it means:
 *
 *   - The returned promise ALWAYS settles by `timeoutMs`. The deadline belongs
 *     to the caller, not to the child's willingness to die.
 *   - SIGTERM at the deadline, then SIGKILL after `killGraceMs`. Escalation is
 *     the whole point; SIGTERM alone is what failed.
 *   - After the grace window the child and its stdio handles are unref'd, so
 *     even a child that outlives SIGKILL (uninterruptible I/O) can never keep
 *     the gateway's event loop — or a test runner — alive.
 *
 * Never pass `timeout` through `options`; it is stripped, because reinstating
 * it would reinstate exactly the bug this module exists to remove.
 */

export interface ExecFileWithTimeoutResult {
  exitCode: number | null;
  stdout: string;
  stderr: string;
  signal: NodeJS.Signals | null;
  timedOut: boolean;
  /** Present when the spawn itself failed (ENOENT and friends). */
  error?: NodeJS.ErrnoException;
}

/** How long a child gets between SIGTERM and SIGKILL. Long enough for a
 *  well-behaved process to flush and exit, short enough that a wedged one is
 *  gone well inside any caller's retry cadence. */
export const DEFAULT_KILL_GRACE_MS = 2_000;

type ExecFileError = NodeJS.ErrnoException & {
  code?: number | string;
  signal?: NodeJS.Signals;
  killed?: boolean;
};

export function execFileWithTimeout(
  command: string,
  args: string[],
  timeoutMs: number,
  options: Omit<ExecFileOptions, "timeout" | "killSignal"> = {},
  killGraceMs: number = DEFAULT_KILL_GRACE_MS,
): Promise<ExecFileWithTimeoutResult> {
  return new Promise((resolve) => {
    let settled = false;
    const settle = (result: ExecFileWithTimeoutResult): void => {
      if (settled) {
        return;
      }
      settled = true;
      resolve(result);
    };

    let deadline: NodeJS.Timeout | undefined;
    let escalation: NodeJS.Timeout | undefined;

    let child: ReturnType<typeof execFile>;
    try {
      child = execFile(
        command,
        args,
        { ...options, windowsHide: true },
        (error, stdout, stderr) => {
          if (deadline) {
            clearTimeout(deadline);
          }
          if (escalation) {
            clearTimeout(escalation);
          }
          const err = error as ExecFileError | null;
          settle({
            exitCode: typeof err?.code === "number" ? err.code : error ? 1 : 0,
            stdout: String(stdout ?? ""),
            stderr: String(stderr ?? ""),
            signal: err?.signal ?? null,
            timedOut: false,
            error: err ?? undefined,
          });
        },
      );
    } catch (error) {
      settle({
        exitCode: 1,
        stdout: "",
        stderr: "",
        signal: null,
        timedOut: false,
        error: error as NodeJS.ErrnoException,
      });
      return;
    }

    /** Drop our references to a child that would not die, so its handles stop
     *  holding the event loop open. Called only after SIGKILL has been sent. */
    const abandon = (): void => {
      const streams = [child.stdout, child.stderr, child.stdin];
      for (const stream of streams) {
        try {
          (stream as unknown as { unref?: () => void } | null)?.unref?.();
        } catch {
          // A stream already destroyed by the runtime has nothing to unref.
        }
      }
      try {
        child.unref();
      } catch {
        // Same.
      }
    };

    deadline = setTimeout(() => {
      try {
        child.kill("SIGTERM");
      } catch {
        // Already gone between the timer firing and this call.
      }
      escalation = setTimeout(() => {
        try {
          child.kill("SIGKILL");
        } catch {
          // Already gone.
        }
        abandon();
      }, Math.max(0, killGraceMs));
      // Unref'd so the grace window itself never delays process exit; the
      // SIGKILL is best-effort insurance, not a reason to stay alive.
      escalation.unref?.();

      // The caller's deadline is honoured here, NOT in the exit handler above:
      // whether the child ever dies is the child's business.
      settle({
        exitCode: null,
        stdout: "",
        stderr: "",
        signal: "SIGTERM",
        timedOut: true,
      });
    }, Math.max(0, timeoutMs));
  });
}

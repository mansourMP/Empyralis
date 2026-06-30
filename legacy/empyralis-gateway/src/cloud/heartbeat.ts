export interface HeartbeatLoopOptions {
  intervalMs: number;
  timeoutMs: number;
  maxConsecutiveFailures?: number;
  sendHeartbeat: () => Promise<void>;
  onHeartbeatFailure?: (error: Error, consecutiveFailures: number) => void | Promise<void>;
  onHeartbeatRecovered?: () => void | Promise<void>;
}

function asError(error: unknown, fallback: string): Error {
  if (error instanceof Error) {
    return error;
  }
  return new Error(String(error || fallback));
}

function intOr(value: number | undefined, fallback: number): number {
  return Number.isFinite(value) ? Number(value) : fallback;
}

export class HeartbeatLoop {
  private timer: NodeJS.Timeout | null = null;
  private running = false;
  private stopped = true;
  private consecutiveFailures = 0;

  start(options: HeartbeatLoopOptions): void {
    this.stop();
    this.stopped = false;
    const intervalMs = Math.max(intOr(options.intervalMs, 1_000), 1_000);
    const timeoutMs = Math.max(intOr(options.timeoutMs, intervalMs), 1_000);
    const maxFailures = Math.max(intOr(options.maxConsecutiveFailures, 3), 1);

    const tick = async () => {
      if (this.stopped) {
        return;
      }
      this.running = true;
      try {
        let timeoutHandle: NodeJS.Timeout | null = null;
        try {
          await Promise.race([
            options.sendHeartbeat(),
            new Promise<never>((_, reject) => {
              timeoutHandle = setTimeout(() => {
                reject(new Error(`Gateway heartbeat timed out after ${timeoutMs}ms.`));
              }, timeoutMs);
            }),
          ]);
        } finally {
          if (timeoutHandle) {
            clearTimeout(timeoutHandle);
          }
        }
        const recovered = this.consecutiveFailures > 0;
        this.consecutiveFailures = 0;
        if (recovered) {
          await options.onHeartbeatRecovered?.();
        }
      } catch (error) {
        this.consecutiveFailures += 1;
        await options.onHeartbeatFailure?.(
          asError(error, "Gateway heartbeat failed."),
          Math.min(this.consecutiveFailures, maxFailures),
        );
      } finally {
        this.running = false;
        if (!this.stopped) {
          this.timer = setTimeout(() => {
            void tick();
          }, intervalMs);
        }
      }
    };

    this.timer = setTimeout(() => {
      void tick();
    }, intervalMs);
  }

  stop(): void {
    this.stopped = true;
    this.running = false;
    this.consecutiveFailures = 0;
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  isRunning(): boolean {
    return this.running && !this.stopped;
  }
}

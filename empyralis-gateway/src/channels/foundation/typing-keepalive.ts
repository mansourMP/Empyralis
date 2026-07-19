/**
 * Generic typing indicator keepalive for personal channels.
 *
 * Repeatedly fires a startAction via the sender callback at keepaliveMs
 * interval.  Automatically stops after maxTtlMs.  If stopAction is provided,
 * it is fired once when stop() is called.
 */

export interface TypingKeepaliveOptions {
  startAction: string;
  stopAction?: string;
  keepaliveMs?: number; // default 3000
  maxTtlMs?: number; // default 60000
}

export class TypingKeepalive {
  private intervalId: ReturnType<typeof setInterval> | null = null;
  private ttlId: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;

  constructor(
    private readonly sender: ((action: string) => Promise<void> | void) | undefined,
    private readonly options: TypingKeepaliveOptions,
  ) {}

  async start(): Promise<void> {
    if (!this.sender || this.stopped) return;
    this.stopped = false;

    const { startAction, keepaliveMs = 3000, maxTtlMs = 60000 } = this.options;

    await Promise.resolve(this.sender(startAction)).catch(() => {});

    // A concurrent stop() can complete while the await above is still in
    // flight (e.g. an inbound message admits a typing session and the
    // runtime is stopped again immediately after, as real callers and
    // tests both do). stop() only clears intervalId/ttlId if they're
    // already set, so without this check the two timers created below
    // would be armed AFTER stop() already ran, and nothing would ever
    // clear them -- a permanently leaked, self-perpetuating interval.
    if (this.stopped) return;

    this.intervalId = setInterval(() => {
      if (this.stopped || !this.sender) return;
      Promise.resolve(this.sender(startAction)).catch(() => {});
    }, keepaliveMs);
    // Best-effort/cosmetic by design (see class doc) -- must never be the
    // reason a process/test can't exit, so this timer is never allowed to
    // keep the event loop alive on its own.
    this.intervalId.unref?.();

    this.ttlId = setTimeout(() => {
      void this.stop();
    }, maxTtlMs);
    this.ttlId.unref?.();
  }

  async stop(): Promise<void> {
    if (this.stopped) return;
    this.stopped = true;

    if (this.intervalId !== null) {
      clearInterval(this.intervalId);
      this.intervalId = null;
    }
    if (this.ttlId !== null) {
      clearTimeout(this.ttlId);
      this.ttlId = null;
    }

    const { stopAction } = this.options;
    if (stopAction && this.sender) {
      await Promise.resolve(this.sender(stopAction)).catch(() => {});
    }
  }
}

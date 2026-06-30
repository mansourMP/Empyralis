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

    this.intervalId = setInterval(() => {
      if (this.stopped || !this.sender) return;
      Promise.resolve(this.sender(startAction)).catch(() => {});
    }, keepaliveMs);

    this.ttlId = setTimeout(() => {
      void this.stop();
    }, maxTtlMs);
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

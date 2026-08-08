/**
 * Bounded, disk-backed retry queue for forwarding inbound events to
 * Empyralis.
 *
 * FAILURE BEHAVIOUR DECISION (requirement #5): queue with capped size and
 * exponential backoff, never silent drop and never unbounded growth.
 *
 *   - Silent drop is unacceptable: a dropped inbound message is a customer's
 *     real conversation Empyralis's brain never sees, with no error anywhere
 *     — exactly the "silent misrouting beats loud failure" failure mode
 *     CLAUDE.md already tracks.
 *   - Unbounded buffering on a customer's machine is unacceptable for the
 *     same reason `GatewayOutbox` (empyralis-gateway/src/state/outbox.ts)
 *     caps at `MAX_OUTBOX_ITEMS = 10_000` rather than growing forever: an
 *     Empyralis outage of any length must not become a customer's full disk.
 *
 * This mirrors `GatewayOutbox`'s vocabulary (bounded, retry-capped,
 * durable-across-restart) deliberately, but is its own small
 * implementation rather than an import: this package runs inside OpenClaw's
 * process as a separate Node process from empyralis-gateway, and must not
 * take on a cross-package dependency on gateway internals (nor on gateway's
 * cloud credentials — see index.ts's auth section). When the drop-oldest
 * path fires, that is logged loudly (never swallowed) so an operator sees a
 * customer's Empyralis outage actually lost messages instead of assuming
 * the queue absorbed it.
 */

import { promises as fs } from "node:fs";
import * as path from "node:path";
import * as crypto from "node:crypto";

export interface QueueItem<T> {
  id: string;
  payload: T;
  attempts: number;
  createdAt: string;
  /**
   * Epoch ms, not an ISO string rendered from the wall clock. Backoff
   * checks in `flushOnce` compare this against the `now()` passed to
   * `flushOnce` (defaulting to `Date.now()` in production, but a fake
   * clock in tests) — mixing a wall-clock timestamp here with a fake `now()`
   * there previously made every retry-after-backoff check compare against
   * the wrong epoch and wedge retries forever. Keep both sides on the same
   * clock.
   */
  lastAttemptAtMs?: number;
  lastError?: string;
}

export interface BoundedRetryQueueOptions<T> {
  filePath: string;
  maxItems: number;
  maxAttempts: number;
  minBackoffMs: number;
  maxBackoffMs: number;
  send: (payload: T) => Promise<void>;
  onDropped?: (item: QueueItem<T>, reason: string) => void;
  onGiveUp?: (item: QueueItem<T>) => void;
  log?: (message: string) => void;
}

function backoffForAttempt(attempt: number, minMs: number, maxMs: number): number {
  const exp = Math.min(maxMs, minMs * 2 ** Math.max(0, attempt - 1));
  return Math.min(maxMs, Math.max(minMs, exp));
}

export class BoundedRetryQueue<T> {
  private items: QueueItem<T>[] = [];
  private loaded = false;
  private flushing = false;
  private timer: NodeJS.Timeout | null = null;
  private stopped = true;

  constructor(private readonly opts: BoundedRetryQueueOptions<T>) {}

  private log(message: string): void {
    this.opts.log?.(message);
  }

  async load(): Promise<void> {
    if (this.loaded) return;
    this.loaded = true;
    try {
      const raw = await fs.readFile(this.opts.filePath, "utf8");
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        this.items = parsed as QueueItem<T>[];
      }
    } catch (err: unknown) {
      const code = (err as NodeJS.ErrnoException | undefined)?.code;
      if (code !== "ENOENT") {
        this.log(`empyralis-bridge: queue file unreadable, starting empty (${String(err)})`);
      }
      this.items = [];
    }
  }

  private async persist(): Promise<void> {
    const dir = path.dirname(this.opts.filePath);
    await fs.mkdir(dir, { recursive: true });
    const tmpPath = `${this.opts.filePath}.tmp-${process.pid}`;
    await fs.writeFile(tmpPath, JSON.stringify(this.items), "utf8");
    await fs.rename(tmpPath, this.opts.filePath);
  }

  async enqueue(payload: T): Promise<void> {
    await this.load();
    const item: QueueItem<T> = {
      id: crypto.randomUUID(),
      payload,
      attempts: 0,
      createdAt: new Date().toISOString(),
    };
    this.items.push(item);
    if (this.items.length > this.opts.maxItems) {
      const dropped = this.items.shift();
      if (dropped) {
        this.log(
          `empyralis-bridge: queue full (max ${this.opts.maxItems}); dropped oldest pending item id=${dropped.id} createdAt=${dropped.createdAt} — Empyralis did not receive this inbound message.`,
        );
        this.opts.onDropped?.(dropped, "queue_full");
      }
    }
    await this.persist();
  }

  size(): number {
    return this.items.length;
  }

  /** Attempt to drain the queue in FIFO order. Stops at the first item still in backoff or that fails, to preserve ordering and avoid hammering a down endpoint. */
  async flushOnce(now: () => number = () => Date.now()): Promise<{ sent: number; remaining: number }> {
    await this.load();
    if (this.flushing) return { sent: 0, remaining: this.items.length };
    this.flushing = true;
    let sent = 0;
    try {
      while (this.items.length > 0) {
        const item = this.items[0];
        if (item.attempts > 0 && item.lastAttemptAtMs !== undefined) {
          const waitedMs = now() - item.lastAttemptAtMs;
          const requiredMs = backoffForAttempt(item.attempts, this.opts.minBackoffMs, this.opts.maxBackoffMs);
          if (waitedMs < requiredMs) break;
        }
        try {
          await this.opts.send(item.payload);
          this.items.shift();
          sent += 1;
          await this.persist();
        } catch (err: unknown) {
          item.attempts += 1;
          item.lastAttemptAtMs = now();
          item.lastError = err instanceof Error ? err.message : String(err);
          if (item.attempts >= this.opts.maxAttempts) {
            this.items.shift();
            this.log(
              `empyralis-bridge: giving up on queued item id=${item.id} after ${item.attempts} attempts (last error: ${item.lastError}) — Empyralis did not receive this inbound message.`,
            );
            this.opts.onGiveUp?.(item);
            await this.persist();
            continue;
          }
          await this.persist();
          break;
        }
      }
    } finally {
      this.flushing = false;
    }
    return { sent, remaining: this.items.length };
  }

  start(intervalMs: number): void {
    if (!this.stopped) return;
    this.stopped = false;
    const tick = async () => {
      if (this.stopped) return;
      try {
        await this.flushOnce();
      } catch (err) {
        this.log(`empyralis-bridge: queue flush loop error: ${String(err)}`);
      }
      if (!this.stopped) {
        this.timer = setTimeout(tick, intervalMs);
        this.timer.unref?.();
      }
    };
    this.timer = setTimeout(tick, 0);
    this.timer.unref?.();
  }

  stop(): void {
    this.stopped = true;
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }
}

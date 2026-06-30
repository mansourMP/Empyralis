import { CloudSessionOutbox } from "./outbox.js";

/**
 * ReplayLoop drains the outbox on reconnect, replaying pending messages
 * through the provided send function.
 *
 * Usage:
 *   const loop = new ReplayLoop({ sessionId, outbox, send: async (item) => { ... } });
 *   await loop.drain();
 *
 * Behavior:
 *  - Drains replayable items in FIFO order (oldest first)
 *  - On send success: acknowledge
 *  - On send failure: markForReplay (retry count ++), up to MAX_RETRIES
 *  - After MAX_RETRIES: abandoned
 *  - Stops if session disconnects mid-drain
 */
export class ReplayLoop {
  /**
   * @param {object} opts
   * @param {string} opts.sessionId
   * @param {CloudSessionOutbox} opts.outbox
   * @param {function} opts.send - async (item) => { ok: boolean, error?: string }
   * @param {function} [opts.isConnected] - () => boolean, stops drain if returns false
   * @param {object} [opts.logger]
   */
  constructor({ sessionId, outbox, send, isConnected, logger }) {
    this.sessionId = sessionId;
    this.outbox = outbox;
    this.send = send;
    this.isConnected = isConnected || (() => true);
    this.logger = logger;
    this.draining = false;
  }

  /**
   * Drain all replayable items. Safe to call multiple times — will not
   * overlap if already draining.
   * @returns {{ replayed: number, acknowledged: number, failed: number }}
   */
  async drain() {
    if (this.draining) {
      this.logger?.info?.("replay drain already in progress, skipping");
      return { replayed: 0, acknowledged: 0, failed: 0 };
    }

    this.draining = true;
    let replayed = 0;
    let acknowledged = 0;
    let failed = 0;

    try {
      const items = await this.outbox.listReplayablePending();
      this.logger?.info?.({ count: items.length, sessionId: this.sessionId }, "replay drain starting");

      for (const item of items) {
        // Stop if session disconnected
        if (!this.isConnected()) {
          this.logger?.warn?.("session disconnected during replay drain, stopping");
          break;
        }

        await this.outbox.markAttemptStarted(item.requestId);

        try {
          const result = await this.send(item);
          replayed++;

          if (result?.ok) {
            await this.outbox.acknowledge(item.requestId);
            acknowledged++;
            this.logger?.info?.({ requestId: item.requestId }, "replay acknowledged");
          } else {
            const err = result?.error || "send returned not ok";
            const opts = {};
            if (result?.retryAfterMs) {
              opts.scheduledAt = new Date(Date.now() + result.retryAfterMs).toISOString();
            }
            await this.outbox.markForReplay(item.requestId, err, opts);
            failed++;
            this.logger?.warn?.({ requestId: item.requestId, error: err, scheduledAt: opts.scheduledAt }, "replay failed, queued for retry");
          }
        } catch (err) {
          await this.outbox.markForReplay(item.requestId, err?.message || "send threw");
          failed++;
          this.logger?.error?.({ requestId: item.requestId, err }, "replay threw, queued for retry");
        }
      }

      // Prune old acknowledged/abandoned items
      const pruned = await this.outbox.prune();
      if (pruned > 0) {
        this.logger?.info?.({ pruned }, "replay pruned stale items");
      }
    } finally {
      this.draining = false;
    }

    const summary = { replayed, acknowledged, failed };
    this.logger?.info?.(summary, "replay drain complete");
    return summary;
  }
}

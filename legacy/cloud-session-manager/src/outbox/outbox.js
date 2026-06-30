import crypto from "node:crypto";
import Redis from "ioredis";
import { CONFIG } from "../config.js";

/**
 * CloudSessionOutbox — Redis-backed outbox for cloud session manager.
 * Ported from empyralis-gateway/src/state/outbox.ts (GatewayOutbox).
 *
 * Each session gets its own Redis key: session:{sessionId}:outbox
 * Items are stored as a JSON array under that key.
 *
 * Statuses: pending | acknowledged | failed | abandoned | uncertain
 *
 * Key behaviors:
 *  - Enqueue deduplicates by requestId
 *  - Max 5 retries before abandoned
 *  - Prune removes acknowledged/abandoned/uncertain items older than 24h
 *  - Idempotency: re-enqueueing same requestId returns existing item
 */

const MAX_OUTBOX_ITEMS = 10_000;
const MAX_RETRIES = 5;
const STALE_AGE_MS = 24 * 60 * 60 * 1000; // 24 hours

export class CloudSessionOutbox {
  /** @param {string} sessionId */
  constructor(sessionId) {
    this.sessionId = sessionId;
    this.key = `session:${sessionId}:outbox`;
    this.redis = new Redis(CONFIG.sessionRedisUrl, {
      lazyConnect: true,
      maxRetriesPerRequest: 2,
      retryStrategy: (times) => Math.min(times * 200, 2000),
    });
  }

  /** Connect to Redis */
  async connect() {
    if (this.redis.status !== "ready" && this.redis.status !== "connecting") {
      await this.redis.connect();
    }
  }

  /** Disconnect from Redis */
  async disconnect() {
    if (this.redis.status !== "end") {
      await this.redis.quit().catch(() => {});
    }
  }

  /** Read all outbox items for this session */
  async list() {
    await this.connect();
    const raw = await this.redis.get(this.key);
    if (!raw) return [];
    try {
      return JSON.parse(raw);
    } catch {
      return [];
    }
  }

  /** Write all outbox items */
  async _write(items) {
    await this.connect();
    await this.redis.set(this.key, JSON.stringify(items));
  }

  /**
   * Enqueue a new outbox item. Deduplicates by requestId.
   * @param {string} requestId
   * @param {string} messageType
   * @param {object} payload
   * @param {object} [options]
   * @param {boolean} [options.replayable=true]
   */
  async enqueue(requestId, messageType, payload, options = {}) {
    const items = await this.list();

    // Deduplicate
    const existing = items.find((item) => item.requestId === requestId);
    if (existing) return existing;

    // Enforce max
    if (items.length >= MAX_OUTBOX_ITEMS) {
      throw new Error(`Outbox full (max ${MAX_OUTBOX_ITEMS}). Cannot enqueue "${requestId}".`);
    }

    const now = new Date().toISOString();
    const item = {
      id: crypto.randomUUID(),
      requestId,
      messageType,
      status: "pending",
      replayable: options.replayable !== false,
      retryCount: 0,
      createdAt: now,
      updatedAt: now,
      lastAttemptAt: now,
      scheduledAt: options.scheduledAt || null,
      payload,
    };

    items.push(item);
    await this._write(items);
    return item;
  }

  /** Mark an item as acknowledged (delivered successfully) */
  async acknowledge(requestId) {
    const items = await this.list();
    const now = new Date().toISOString();
    const updated = items.map((item) =>
      item.requestId === requestId
        ? { ...item, status: "acknowledged", updatedAt: now }
        : item
    );
    await this._write(updated);
  }

  /** Mark an attempt as started */
  async markAttemptStarted(requestId) {
    const items = await this.list();
    const now = new Date().toISOString();
    const updated = items.map((item) =>
      item.requestId === requestId
        ? { ...item, status: "pending", lastAttemptAt: now, updatedAt: now }
        : item
    );
    await this._write(updated);
  }

  /** Mark as failed (non-replayable) */
  async markAttemptFailed(requestId, errorMessage) {
    await this._updateFailureState(requestId, errorMessage, "failed");
  }

  /** Mark as uncertain (non-replayable, unknown outcome) */
  async markUncertain(requestId, errorMessage) {
    await this._updateFailureState(requestId, errorMessage, "uncertain");
  }

  /** Mark for replay — keeps status "pending" and increments retry */
  async markForReplay(requestId, errorMessage, opts = {}) {
    const scheduledAt = opts?.scheduledAt || null;
    await this._updateFailureState(requestId, errorMessage, "pending", true, scheduledAt);
  }

  /** List items eligible for replay (pending, not abandoned, not ack'd) */
  async listReplayablePending() {
    const items = await this.list();
    return items
      .filter((item) => {
        if (!item.replayable) return false;
        if (item.status === "acknowledged") return false;
        if (item.status === "abandoned") return false;
        if (item.status === "uncertain") return false;
        // Skip future-scheduled items (rate-limited, not ready yet)
        if (item.scheduledAt) {
          const scheduled = Date.parse(item.scheduledAt);
          if (Number.isFinite(scheduled) && scheduled > Date.now()) return false;
        }
        return true; // pending, failed — both replayable
      })
      .sort((a, b) => {
        const aTime = Date.parse(a.createdAt || "");
        const bTime = Date.parse(b.createdAt || "");
        return (Number.isFinite(aTime) ? aTime : 0) - (Number.isFinite(bTime) ? bTime : 0);
      });
  }

  /** Prune acknowledged/abandoned/uncertain items older than 24h */
  async prune() {
    const items = await this.list();
    const cutoff = Date.now() - STALE_AGE_MS;
    const filtered = items.filter((item) => {
      if (item.status !== "acknowledged" && item.status !== "abandoned" && item.status !== "uncertain") {
        return true;
      }
      const updated = Date.parse(item.updatedAt);
      return Number.isFinite(updated) && updated > cutoff;
    });
    const removed = items.length - filtered.length;
    if (removed > 0) {
      await this._write(filtered);
    }
    return removed;
  }

  /** Get summary counts */
  async summarize() {
    const items = await this.list();
    return {
      total: items.length,
      pending: items.filter((i) => i.status === "pending").length,
      failed: items.filter((i) => i.status === "failed").length,
      acknowledged: items.filter((i) => i.status === "acknowledged").length,
      uncertain: items.filter((i) => i.status === "uncertain").length,
      abandoned: items.filter((i) => i.status === "abandoned").length,
    };
  }

  /** Clear the entire outbox (for testing) */
  async clear() {
    await this.connect();
    await this.redis.del(this.key);
  }

  /** Get a single item by requestId */
  async get(requestId) {
    const items = await this.list();
    return items.find((item) => item.requestId === requestId) ?? null;
  }

  // ── Internal ────────────────────────────────────────────────

  async _updateFailureState(requestId, errorMessage, nextStatus, keepReplayablePending = false, scheduledAt = null) {
    const items = await this.list();
    const now = new Date().toISOString();
    const updated = items.map((item) => {
      if (item.requestId !== requestId) return item;
      if (item.status === "acknowledged" || item.status === "abandoned" || item.status === "uncertain") {
        return item;
      }
      const newRetryCount = item.retryCount + 1;
      const tooManyRetries = keepReplayablePending && newRetryCount >= MAX_RETRIES;
      return {
        ...item,
        status: tooManyRetries ? "abandoned" : keepReplayablePending ? "pending" : nextStatus,
        retryCount: newRetryCount,
        lastAttemptAt: now,
        lastError: String(errorMessage || "").trim() || "Request failed.",
        updatedAt: now,
        scheduledAt: scheduledAt || item.scheduledAt || null,
      };
    });
    await this._write(updated);
  }
}

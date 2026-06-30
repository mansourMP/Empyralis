import crypto from "crypto";

import { GatewayStateDb } from "./db";

export interface GatewayOutboxItem {
  id: string;
  requestId: string;
  messageType: string;
  status: "pending" | "acknowledged" | "failed" | "abandoned" | "uncertain";
  replayable: boolean;
  retryCount: number;
  createdAt: string;
  updatedAt: string;
  lastAttemptAt: string;
  lastError?: string;
  payload: Record<string, unknown>;
}

export interface GatewayOutboxSummary {
  total: number;
  pending: number;
  failed: number;
  acknowledged: number;
  uncertain: number;
}

export const MAX_OUTBOX_ITEMS = 10_000;
export const MAX_RETRIES = 5;
const STALE_AGE_MS = 24 * 60 * 60 * 1000; // 24 hours

export class GatewayOutbox {
  constructor(private readonly db: GatewayStateDb) {}

  async list(): Promise<GatewayOutboxItem[]> {
    return this.db.readJson<GatewayOutboxItem[]>("outbox.json", []);
  }

  async get(requestId: string): Promise<GatewayOutboxItem | null> {
    const token = String(requestId || "").trim();
    if (!token) {
      return null;
    }
    const items = await this.list();
    return items.find((item) => item.requestId === token) ?? null;
  }

  async enqueue(
    requestId: string,
    messageType: string,
    payload: Record<string, unknown>,
    options: { replayable?: boolean } = {},
  ): Promise<GatewayOutboxItem> {
    const items = await this.list();

    // Deduplicate by requestId
    const existing = items.find((item) => item.requestId === requestId);
    if (existing) {
      return existing;
    }

    // Enforce max size
    if (items.length >= MAX_OUTBOX_ITEMS) {
      throw new Error(
        `Outbox is full (max ${MAX_OUTBOX_ITEMS} items). Cannot enqueue request "${requestId}".`,
      );
    }

    const now = new Date().toISOString();
    const item: GatewayOutboxItem = {
      id: crypto.randomUUID(),
      requestId,
      messageType,
      status: "pending",
      replayable: options.replayable !== false,
      retryCount: 0,
      createdAt: now,
      updatedAt: now,
      lastAttemptAt: now,
      payload,
    };
    items.push(item);
    await this.db.writeJson("outbox.json", items);
    return item;
  }

  async markAttemptStarted(requestId: string): Promise<void> {
    const items = await this.list();
    const now = new Date().toISOString();
    const next = items.map((item) =>
      item.requestId === requestId
        ? {
            ...item,
            status: "pending" as const,
            lastAttemptAt: now,
            updatedAt: now,
          }
        : item,
    );
    await this.db.writeJson("outbox.json", next);
  }

  async acknowledge(requestId: string): Promise<void> {
    const items = await this.list();
    const now = new Date().toISOString();
    const next = items.map((item) =>
      item.requestId === requestId
        ? { ...item, status: "acknowledged" as const, updatedAt: now }
        : item,
    );
    await this.db.writeJson("outbox.json", next);
  }

  async markAttemptFailed(requestId: string, errorMessage: string): Promise<void> {
    await this.updateFailureState(requestId, errorMessage, "failed");
  }

  async markUncertain(requestId: string, errorMessage: string): Promise<void> {
    await this.updateFailureState(requestId, errorMessage, "uncertain");
  }

  async markForReplay(requestId: string, errorMessage: string): Promise<void> {
    await this.updateFailureState(requestId, errorMessage, "pending", true);
  }

  async listReplayablePending(): Promise<GatewayOutboxItem[]> {
    const items = await this.list();
    return items
      .filter(
        (item) =>
          item.replayable &&
          item.status !== "acknowledged" &&
          item.status !== "abandoned" &&
          item.status !== "uncertain",
      )
      .sort((left, right) => {
        const leftTime = Date.parse(left.createdAt || "");
        const rightTime = Date.parse(right.createdAt || "");
        return (Number.isFinite(leftTime) ? leftTime : 0) - (Number.isFinite(rightTime) ? rightTime : 0);
      });
  }

  /**
   * Remove items with status "acknowledged" or "abandoned" older than 24h.
   */
  async prune(): Promise<number> {
    const items = await this.list();
    const cutoff = Date.now() - STALE_AGE_MS;
    const filtered = items.filter((item) => {
      if (item.status !== "acknowledged" && item.status !== "abandoned" && item.status !== "uncertain") {
        return true;
      }
      const updatedTime = Date.parse(item.updatedAt);
      return Number.isFinite(updatedTime) && updatedTime > cutoff;
    });
    const removed = items.length - filtered.length;
    if (removed > 0) {
      await this.db.writeJson("outbox.json", filtered);
    }
    return removed;
  }

  async summarize(): Promise<GatewayOutboxSummary> {
    const items = await this.list();
    return {
      total: items.length,
      pending: items.filter((item) => item.status === "pending").length,
      failed: items.filter((item) => item.status === "failed").length,
      acknowledged: items.filter((item) => item.status === "acknowledged").length,
      uncertain: items.filter((item) => item.status === "uncertain").length,
    };
  }

  private async updateFailureState(
    requestId: string,
    errorMessage: string,
    nextStatus: GatewayOutboxItem["status"],
    keepReplayablePending = false,
  ): Promise<void> {
    const items = await this.list();
    const now = new Date().toISOString();
    const next = items.map((item) => {
      if (item.requestId !== requestId) {
        return item;
      }
      if (item.status === "acknowledged" || item.status === "abandoned" || item.status === "uncertain") {
        return item;
      }
      const newRetryCount = item.retryCount + 1;

      // If retries exceed max, mark as abandoned
      const tooManyRetries = keepReplayablePending && newRetryCount >= MAX_RETRIES;

      return {
        ...item,
        status: tooManyRetries ? ("abandoned" as const) : keepReplayablePending ? ("pending" as const) : nextStatus,
        retryCount: newRetryCount,
        lastAttemptAt: now,
        lastError: String(errorMessage || "").trim() || "Gateway request failed.",
        updatedAt: now,
      };
    });
    await this.db.writeJson("outbox.json", next);
  }
}

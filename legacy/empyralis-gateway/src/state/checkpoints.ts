import { GatewayStateDb, GatewayStateSnapshot } from "./db";

export type GatewayHealthState = "online" | "offline" | "reconnecting" | "degraded";

const DEBOUNCE_WINDOW_MS = 100;

interface PendingWrite {
  snapshot: GatewayStateSnapshot;
  timer: NodeJS.Timeout | null;
}

export class GatewayCheckpoints {
  private readonly pendingWrites = new Map<string, PendingWrite>();

  constructor(private readonly db: GatewayStateDb) {}

  async load(): Promise<GatewayStateSnapshot> {
    return this.db.readJson<GatewayStateSnapshot>("checkpoints.json", {});
  }

  async save(snapshot: GatewayStateSnapshot): Promise<GatewayStateSnapshot> {
    const current = await this.load();
    const merged: GatewayStateSnapshot = {
      ...current,
      ...snapshot,
      updatedAt: new Date().toISOString(),
    };
    return this.debouncedWrite(merged);
  }

  async saveHealthState(
    healthState: GatewayHealthState,
    snapshot: Omit<GatewayStateSnapshot, "healthState"> = {},
  ): Promise<GatewayStateSnapshot> {
    return this.save({
      ...snapshot,
      healthState,
    });
  }

  async markRecovered(snapshot: GatewayStateSnapshot = {}): Promise<GatewayStateSnapshot> {
    return this.save({
      ...snapshot,
      resumeReady: true,
      lastRecoveryAt: new Date().toISOString(),
      lastDisconnectReason: undefined,
    });
  }

  /**
   * Force an immediate write, bypassing the debounce.
   */
  async flush(): Promise<void> {
    const cacheKey = "checkpoints.json";
    const pending = this.pendingWrites.get(cacheKey);
    if (pending) {
      if (pending.timer) {
        clearTimeout(pending.timer);
        pending.timer = null;
      }
      this.pendingWrites.delete(cacheKey);
      await this.db.writeJson(cacheKey, pending.snapshot);
    }
  }

  private async debouncedWrite(snapshot: GatewayStateSnapshot): Promise<GatewayStateSnapshot> {
    const cacheKey = "checkpoints.json";
    const existing = this.pendingWrites.get(cacheKey);

    if (existing) {
      // Merge into existing pending write
      existing.snapshot = {
        ...existing.snapshot,
        ...snapshot,
      };
      if (existing.timer) {
        clearTimeout(existing.timer);
      }
      existing.timer = setTimeout(() => {
        existing.timer = null;
        this.pendingWrites.delete(cacheKey);
        void this.db.writeJson(cacheKey, existing.snapshot);
      }, DEBOUNCE_WINDOW_MS);
      existing.timer.unref?.();
      return existing.snapshot;
    }

    const pending: PendingWrite = {
      snapshot,
      timer: null as unknown as ReturnType<typeof setTimeout>,
    };
    pending.timer = setTimeout(() => {
      pending.timer = null;
      this.pendingWrites.delete(cacheKey);
      void this.db.writeJson(cacheKey, pending.snapshot);
    }, DEBOUNCE_WINDOW_MS);
    pending.timer.unref?.();
    this.pendingWrites.set(cacheKey, pending);
    return snapshot;
  }
}

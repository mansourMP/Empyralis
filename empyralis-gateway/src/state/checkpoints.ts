import { GatewayStateDb, GatewayStateSnapshot } from "./db";

export type GatewayHealthState = "online" | "offline" | "reconnecting" | "degraded";

const DEBOUNCE_WINDOW_MS = 100;

interface PendingWrite {
  snapshot: GatewayStateSnapshot;
  timer: NodeJS.Timeout | null;
}

export class GatewayCheckpoints {
  private readonly pendingWrites = new Map<string, PendingWrite>();
  // In-memory mirror of the last healthState written via save()/
  // saveHealthState(), kept synchronously readable so hot paths (the
  // heartbeat payload builder) don't need an async disk read — and so they
  // see a state that was just written even inside the debounced-write
  // window (see debouncedWrite() below). Defaults to "offline": nothing
  // has confirmed a live connection yet.
  private lastKnownHealthState: GatewayHealthState = "offline";

  constructor(private readonly db: GatewayStateDb) {}

  /** The most recently recorded health state, synchronously available.
   *  This is what GatewayWsClient.sendHeartbeat() threads into the
   *  heartbeat payload's `health_state` field instead of a hardcoded
   *  literal — see heartbeat-payload.ts. */
  currentHealthState(): GatewayHealthState {
    return this.lastKnownHealthState;
  }

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
    if (merged.healthState) {
      this.lastKnownHealthState = merged.healthState;
    }
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

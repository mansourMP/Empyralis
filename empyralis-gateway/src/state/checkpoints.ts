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
  // In-memory mirror of lastClientSeq, for EXACTLY the reason
  // lastKnownHealthState above exists — save() is debounced by 100ms, so a
  // load() inside that window returns the value from before the write. Callers
  // that allocated an outbound frame sequence with
  // `(await load()).lastClientSeq + 1` therefore handed the SAME seq to two
  // messages arriving less than 100ms apart, and the cloud answers a repeated
  // seq by closing the socket (gateway_protocol_service.py, "gateway frame
  // replay detected", code 4408) and losing the second message. `null` until
  // the first allocation seeds it from disk; after that disk is a durability
  // record this class writes and never reads back.
  private lastKnownClientSeq: number | null = null;
  // Serializes allocateClientSeq's own seed-then-increment, so the very first
  // two concurrent allocations cannot both find the mirror unseeded.
  private clientSeqGate: Promise<unknown> = Promise.resolve();

  constructor(private readonly db: GatewayStateDb) {}

  /**
   * The next outbound frame sequence number, allocated exactly once.
   *
   * THE INVARIANT: every value this returns is strictly greater than every
   * value returned before it, for the life of the process and across a
   * restart. The cloud enforces it as a connection-fatal rule, so a duplicate
   * is not a retry — it is a dropped customer message plus a dropped socket.
   *
   * Never reintroduce `(await load()).lastClientSeq + 1` at a call site: it
   * has two independent failure modes, and this method closes both. The read
   * and the write are separated by an await (so two callers interleave), and
   * the write is debounced (so even a serialized second caller reads a stale
   * number back). Reproduced live 2026-08-08 on the first provisioned OpenClaw
   * instance — two inbound channel messages 23ms apart, both `seq: 1`.
   */
  async allocateClientSeq(): Promise<number> {
    const allocation = this.clientSeqGate.then(async () => {
      if (this.lastKnownClientSeq === null) {
        const snapshot = await this.load();
        this.lastKnownClientSeq = Math.max(Number(snapshot.lastClientSeq ?? 0), 0);
      }
      const next = this.lastKnownClientSeq + 1;
      this.lastKnownClientSeq = next;
      // Durability only. A failure here must not hand the caller a seq it
      // cannot use, and must not stall the lane: the worst case of a lost
      // write is that a later process restart re-uses a number, which is the
      // same position we are in without any persistence at all.
      await this.save({ lastClientSeq: next }).catch(() => undefined);
      return next;
    });
    this.clientSeqGate = allocation.catch(() => undefined);
    return allocation;
  }

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

import { GatewayStateDb } from "./db";

/** A "response" frame this Gateway owes the backend for some earlier inbound
 *  request (tool.invoke, tool.interrupt, channel.outbound) — queued here
 *  whenever the socket wasn't open (or the send itself failed) at the moment
 *  the underlying work finished, so the result isn't silently dropped just
 *  because the WS connection that carried the original request is gone by
 *  the time the answer is ready (codex exec alone can run up to ~120s).
 *
 *  Unlike GatewayOutbox (which queues OUTBOUND REQUESTS this Gateway
 *  initiates, and has to worry about replay/idempotency because re-sending
 *  a request can re-trigger work), a response is inert data once computed —
 *  delivering it twice is harmless, so this queue only needs "keep retrying
 *  until it's out the door," not the outbox's fuller state machine. */
export interface PendingGatewayResponse {
  requestId: string;
  frame: Record<string, unknown>;
  createdAt: string;
  attempts: number;
}

export class PendingResponseQueue {
  constructor(private readonly db: GatewayStateDb) {}

  async list(): Promise<PendingGatewayResponse[]> {
    return this.db.readJson<PendingGatewayResponse[]>("pending-responses.json", []);
  }

  private async save(items: PendingGatewayResponse[]): Promise<void> {
    await this.db.writeJson("pending-responses.json", items);
  }

  async enqueue(requestId: string, frame: Record<string, unknown>): Promise<void> {
    const token = String(requestId || "").trim();
    if (!token) {
      return;
    }
    const items = await this.list();
    const withoutExisting = items.filter((item) => item.requestId !== token);
    withoutExisting.push({
      requestId: token,
      frame,
      createdAt: new Date().toISOString(),
      attempts: 0,
    });
    await this.save(withoutExisting);
  }

  async remove(requestId: string): Promise<void> {
    const token = String(requestId || "").trim();
    if (!token) {
      return;
    }
    const items = await this.list();
    const next = items.filter((item) => item.requestId !== token);
    if (next.length !== items.length) {
      await this.save(next);
    }
  }

  /** Attempts to deliver every queued response via `send`, stopping at the
   *  first failure (if the connection just died again, the rest would fail
   *  the same way — no point burning through the whole queue). Delivered
   *  entries are removed as they succeed, so a partial flush still makes
   *  real progress instead of all-or-nothing. */
  async flush(send: (frame: Record<string, unknown>) => Promise<void>): Promise<void> {
    const items = await this.list();
    if (items.length === 0) {
      return;
    }
    for (const item of items) {
      try {
        await send(item.frame);
        await this.remove(item.requestId);
      } catch {
        return;
      }
    }
  }
}

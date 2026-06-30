/**
 * Generic outbound message persistence backed by a JSON file.
 *
 * Parameterized by a payload type so channels can extend with
 * channel-specific fields (e.g. WhatsApp clientMessageId).
 */

import type { GatewayStateDb } from "../../state/db";

export interface OutboundRecord {
  idempotencyKey: string;
  remoteJid: string;
  text: string;
  replyToExternalMessageId?: string;
  status: "pending" | "delivered";
  externalMessageId?: string;
  attemptCount: number;
  lastAttemptAt?: string;
  createdAt: string;
  updatedAt: string;
}

export class OutboundStore<TPayload extends Record<string, unknown> = Record<string, unknown>> {
  constructor(
    private readonly db: GatewayStateDb,
    private readonly fileName: string,
  ) {}

  async list(): Promise<(OutboundRecord & TPayload)[]> {
    const items = await this.db.readJson<Partial<OutboundRecord & TPayload>[]>(this.fileName, []);
    return items.map((r) => this.normalizeRecord(r));
  }

  async get(idempotencyKey: string): Promise<(OutboundRecord & TPayload) | undefined> {
    const items = await this.list();
    return items.find((i) => i.idempotencyKey === idempotencyKey);
  }

  async beginSend(
    idempotencyKey: string,
    payload: OutboundRecord & TPayload,
  ): Promise<OutboundRecord & TPayload> {
    const items = await this.list();
    const existing = items.find((i) => i.idempotencyKey === idempotencyKey);
    if (existing) {
      const patched = await this.onBeginSendExisting(existing, payload);
      if (patched) {
        await this.db.writeJson(this.fileName, items);
      }
      return patched ?? existing;
    }

    const now = new Date().toISOString();
    const record: OutboundRecord & TPayload = {
      ...payload,
      status: "pending",
      attemptCount: 0,
      createdAt: now,
      updatedAt: now,
    };
    items.push(record);
    await this.db.writeJson(this.fileName, items);
    return record;
  }

  async markAttemptStarted(idempotencyKey: string): Promise<(OutboundRecord & TPayload) | undefined> {
    const items = await this.list();
    const idx = items.findIndex((i) => i.idempotencyKey === idempotencyKey);
    if (idx < 0) return undefined;
    items[idx] = {
      ...items[idx],
      attemptCount: items[idx].attemptCount + 1,
      lastAttemptAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    await this.db.writeJson(this.fileName, items);
    return items[idx];
  }

  async markDelivered(
    idempotencyKey: string,
    externalMessageId?: string,
  ): Promise<(OutboundRecord & TPayload) | undefined> {
    const items = await this.list();
    const idx = items.findIndex((i) => i.idempotencyKey === idempotencyKey);
    if (idx < 0) return undefined;
    items[idx] = {
      ...items[idx],
      status: "delivered",
      externalMessageId: externalMessageId ?? items[idx].externalMessageId,
      updatedAt: new Date().toISOString(),
    };
    await this.db.writeJson(this.fileName, items);
    return items[idx];
  }

  /** Hook: called inside beginSend when a record already exists. */
  protected async onBeginSendExisting(
    existing: OutboundRecord & TPayload,
    _payload: OutboundRecord & TPayload,
  ): Promise<(OutboundRecord & TPayload) | undefined> {
    return undefined; // default: return existing unchanged
  }

  protected normalizeRecord(
    record: Partial<OutboundRecord & TPayload>,
  ): OutboundRecord & TPayload {
    return {
      ...record,
      idempotencyKey: String(record.idempotencyKey ?? ""),
      remoteJid: String(record.remoteJid ?? ""),
      text: String(record.text ?? ""),
      status: (record.status === "delivered" ? "delivered" : "pending") as "pending" | "delivered",
      attemptCount: Number(record.attemptCount ?? 0),
      createdAt: String(record.createdAt ?? new Date().toISOString()),
      updatedAt: String(record.updatedAt ?? new Date().toISOString()),
    } as OutboundRecord & TPayload;
  }
}

import { GatewayStateDb } from "../state/db";

export interface GatewayBrowserSession {
  browserSessionId: string;
  status: string;
  executionTarget: string;
  sessionProfile?: string;
  currentUrl?: string;
  manualTakeover: boolean;
  resumeSupported: boolean;
  reviewedApprovalRequired: boolean;
  reviewedApproved: boolean;
  immutablePlanHash?: string;
  executionBinding?: Record<string, unknown>;
  checkpoint: Record<string, unknown>;
  snapshot: Record<string, unknown>;
  metadata: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
  interruptedAt?: string;
  fallbackReadyAt?: string;
}

export class GatewayBrowserSessionStore {
  constructor(private readonly db: GatewayStateDb) {}

  async list(): Promise<GatewayBrowserSession[]> {
    return this.db.readJson<GatewayBrowserSession[]>("browser-sessions.json", []);
  }

  async get(browserSessionId: string): Promise<GatewayBrowserSession | undefined> {
    const token = String(browserSessionId ?? "").trim();
    if (!token) {
      return undefined;
    }
    const items = await this.list();
    return items.find((item) => item.browserSessionId === token);
  }

  async upsert(session: GatewayBrowserSession): Promise<GatewayBrowserSession> {
    const items = await this.list();
    const now = new Date().toISOString();
    const nextSession: GatewayBrowserSession = {
      ...session,
      updatedAt: now,
      createdAt: session.createdAt || now,
    };
    const next = items.filter((item) => item.browserSessionId !== nextSession.browserSessionId);
    next.push(nextSession);
    await this.db.writeJson("browser-sessions.json", next);
    return nextSession;
  }
}

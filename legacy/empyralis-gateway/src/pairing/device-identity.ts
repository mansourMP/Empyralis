import crypto from "crypto";

import { GatewayStateDb } from "../state/db";

export interface GatewayDeviceIdentity {
  gatewayId: string;
  deviceId: string;
  tenantId?: string;
  workspaceId?: string;
  userId?: string;
  createdAt: string;
  updatedAt: string;
}

export async function resolveDeviceIdentity(
  db: GatewayStateDb,
  hints: Partial<GatewayDeviceIdentity> = {},
): Promise<GatewayDeviceIdentity> {
  const current = await db.readJson<GatewayDeviceIdentity | null>("identity.json", null);
  const now = new Date().toISOString();
  const next: GatewayDeviceIdentity = {
    gatewayId: String(hints.gatewayId || current?.gatewayId || `gateway_${crypto.randomUUID()}`).trim(),
    deviceId: String(hints.deviceId || current?.deviceId || `device_${crypto.randomUUID()}`).trim(),
    tenantId: normalizeOptional(hints.tenantId || current?.tenantId),
    workspaceId: normalizeOptional(hints.workspaceId || current?.workspaceId),
    userId: normalizeOptional(hints.userId || current?.userId),
    createdAt: current?.createdAt || now,
    updatedAt: now,
  };
  await db.writeJson("identity.json", next);
  return next;
}

export async function persistDeviceIdentityScope(
  db: GatewayStateDb,
  scope: Partial<Pick<GatewayDeviceIdentity, "tenantId" | "workspaceId" | "userId" | "gatewayId" | "deviceId">>,
): Promise<GatewayDeviceIdentity> {
  return resolveDeviceIdentity(db, {
    gatewayId: normalizeOptional(scope.gatewayId),
    deviceId: normalizeOptional(scope.deviceId),
    tenantId: normalizeOptional(scope.tenantId),
    workspaceId: normalizeOptional(scope.workspaceId),
    userId: normalizeOptional(scope.userId),
  });
}

function normalizeOptional(value: string | undefined): string | undefined {
  const token = String(value || "").trim();
  return token || undefined;
}

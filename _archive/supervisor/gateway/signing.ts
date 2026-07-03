import crypto from "crypto";

export interface GatewaySupervisorExecuteSignatureInput {
  requestId: string;
  capabilityId: string;
  runId: string;
  traceId: string;
  workspaceId: string;
  arguments: Record<string, unknown>;
  runtimeAccessMode?: string;
  empyralisApproved?: boolean;
  agentScope?: string;
  policy?: Record<string, unknown> | null;
  nonce: string;
  expiresAt: string;
}

export interface GatewaySupervisorInterruptSignatureInput {
  requestId: string;
  runId: string;
  targetRequestId?: string;
  workspaceId: string;
  nonce: string;
  expiresAt: string;
}

function sign(secret: string, payload: string): string {
  return crypto.createHmac("sha256", secret).update(payload, "utf8").digest("hex");
}

function canonicalJson(value: unknown): string {
  if (value === null || value === undefined) {
    return "null";
  }
  if (typeof value === "string") {
    return JSON.stringify(value);
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  }
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    const keys = Object.keys(record).sort();
    return `{${keys.map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(",")}}`;
  }
  return JSON.stringify(String(value));
}

export function signSupervisorExecuteRequest(
  secret: string,
  input: GatewaySupervisorExecuteSignatureInput,
): string {
  return sign(secret, canonicalJson({
    type: "execute",
    version: "v2",
    request_id: input.requestId,
    capability_id: input.capabilityId,
    run_id: input.runId,
    trace_id: input.traceId,
    workspace_id: input.workspaceId,
    arguments: input.arguments ?? {},
    runtime_access_mode: String(input.runtimeAccessMode ?? ""),
    empyralis_approved: Boolean(input.empyralisApproved),
    agent_scope: String(input.agentScope ?? ""),
    policy: input.policy ?? null,
    nonce: input.nonce,
    expires_at: input.expiresAt,
  }));
}

export function signSupervisorInterruptRequest(
  secret: string,
  input: GatewaySupervisorInterruptSignatureInput,
): string {
  return sign(
    secret,
    `interrupt:${input.requestId}:${input.runId}:${String(input.targetRequestId ?? "").trim()}:${input.workspaceId}:${input.nonce}:${input.expiresAt}`,
  );
}

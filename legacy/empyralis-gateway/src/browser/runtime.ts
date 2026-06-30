import type { GatewayRequestEnvelope, GatewayToolInvokePayload } from "../protocol/types";
import { GatewayStateDb } from "../state/db";
import { GatewayBrowserSession, GatewayBrowserSessionStore } from "./session-store";
import { GatewayBrowserWorker } from "./worker";

const BROWSER_SESSION_START_CAPABILITY = "browser.session.start";
const BROWSER_SESSION_ACTION_CAPABILITY = "browser.session.action";
const BROWSER_SESSION_TAKEOVER_CAPABILITY = "browser.session.takeover";
const BROWSER_SESSION_RESUME_CAPABILITY = "browser.session.resume";
const BROWSER_SESSION_INTERRUPT_CAPABILITY = "browser.session.interrupt";

const SUPPORTED_CAPABILITIES = [
  BROWSER_SESSION_START_CAPABILITY,
  BROWSER_SESSION_ACTION_CAPABILITY,
  BROWSER_SESSION_TAKEOVER_CAPABILITY,
  BROWSER_SESSION_RESUME_CAPABILITY,
  BROWSER_SESSION_INTERRUPT_CAPABILITY,
];

function requireObject(value: unknown, message: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(message);
  }
  return value as Record<string, unknown>;
}

function token(value: unknown): string {
  return String(value ?? "").trim();
}

function boolValue(value: unknown): boolean {
  return value === true || value === 1 || value === "1";
}

function fromCloudSession(payload: Record<string, unknown>): GatewayBrowserSession {
  const checkpoint =
    payload.checkpoint && typeof payload.checkpoint === "object" && !Array.isArray(payload.checkpoint)
      ? (payload.checkpoint as Record<string, unknown>)
      : {};
  const snapshot =
    payload.snapshot && typeof payload.snapshot === "object" && !Array.isArray(payload.snapshot)
      ? (payload.snapshot as Record<string, unknown>)
      : {};
  const metadata =
    payload.metadata && typeof payload.metadata === "object" && !Array.isArray(payload.metadata)
      ? (payload.metadata as Record<string, unknown>)
      : {};
  const executionBinding =
    payload.execution_binding && typeof payload.execution_binding === "object" && !Array.isArray(payload.execution_binding)
      ? (payload.execution_binding as Record<string, unknown>)
      : undefined;
  const createdAt = token(payload.created_at) || new Date().toISOString();
  return {
    browserSessionId: token(payload.browser_session_id),
    status: token(payload.status) || "active",
    executionTarget: token(payload.execution_target) || "local_gateway",
    sessionProfile: token(payload.session_profile) || undefined,
    currentUrl: token(payload.current_url) || undefined,
    manualTakeover: boolValue(payload.manual_takeover),
    resumeSupported: boolValue(payload.resume_supported) || Object.keys(checkpoint).length > 0,
    reviewedApprovalRequired: boolValue(payload.reviewed_approval_required) || boolValue(metadata.browser_reviewed_approval_required),
    reviewedApproved: boolValue(payload.reviewed_approved) || boolValue(metadata.browser_reviewed_approved),
    immutablePlanHash: token(payload.immutable_plan_hash) || token(metadata.browser_immutable_plan_hash) || undefined,
    executionBinding,
    checkpoint,
    snapshot,
    metadata,
    createdAt,
    updatedAt: token(payload.updated_at) || createdAt,
    interruptedAt: token(payload.interrupted_at) || undefined,
    fallbackReadyAt: token(payload.fallback_ready_at) || undefined,
  };
}

function toCloudSession(session: GatewayBrowserSession): Record<string, unknown> {
  return {
    browser_session_id: session.browserSessionId,
    status: session.status,
    execution_target: session.executionTarget,
    session_profile: session.sessionProfile,
    current_url: session.currentUrl,
    manual_takeover: session.manualTakeover,
    resume_supported: session.resumeSupported,
    reviewed_approval_required: session.reviewedApprovalRequired,
    reviewed_approved: session.reviewedApproved,
    immutable_plan_hash: session.immutablePlanHash,
    execution_binding: session.executionBinding ?? {},
    checkpoint: session.checkpoint,
    snapshot: session.snapshot,
    metadata: session.metadata,
    created_at: session.createdAt,
    updated_at: session.updatedAt,
    interrupted_at: session.interruptedAt,
    fallback_ready_at: session.fallbackReadyAt,
  };
}

export class GatewayBrowserRuntime {
  private readonly sessions: GatewayBrowserSessionStore;
  private readonly worker: GatewayBrowserWorker;

  constructor(db: GatewayStateDb, worker: GatewayBrowserWorker) {
    this.sessions = new GatewayBrowserSessionStore(db);
    this.worker = worker;
  }

  requestedCapabilities(): string[] {
    return [...SUPPORTED_CAPABILITIES];
  }

  supportsCapability(capabilityId: string): boolean {
    return SUPPORTED_CAPABILITIES.includes(String(capabilityId ?? "").trim());
  }

  async handleCapabilityInvoke(
    frame: GatewayRequestEnvelope<GatewayToolInvokePayload>,
  ): Promise<Record<string, unknown>> {
    const payload = requireObject(frame.payload, "tool.invoke payload must be an object.");
    const capabilityId = token(payload.capability_id);
    const argumentsPayload = requireObject(payload.arguments ?? {}, "arguments must be an object.");
    if (capabilityId === BROWSER_SESSION_START_CAPABILITY) {
      return this.handleStart(argumentsPayload);
    }
    if (capabilityId === BROWSER_SESSION_ACTION_CAPABILITY) {
      return this.handleAction(argumentsPayload);
    }
    if (capabilityId === BROWSER_SESSION_TAKEOVER_CAPABILITY) {
      return this.handleTakeover(argumentsPayload);
    }
    if (capabilityId === BROWSER_SESSION_RESUME_CAPABILITY) {
      return this.handleResume(argumentsPayload);
    }
    if (capabilityId === BROWSER_SESSION_INTERRUPT_CAPABILITY) {
      return this.handleInterrupt(argumentsPayload);
    }
    throw new Error(`Unsupported browser capability: ${capabilityId || "unknown"}`);
  }

  private async handleStart(argumentsPayload: Record<string, unknown>): Promise<Record<string, unknown>> {
    const response = await this.worker.send("start_session", {
      url: token(argumentsPayload.url) || undefined,
      session_profile: token(argumentsPayload.session_profile) || undefined,
      session_mode: token(argumentsPayload.session_mode) || undefined,
      attach_endpoint_url: token(argumentsPayload.attach_endpoint_url) || undefined,
      browser_metadata: requireObject(argumentsPayload.browser_metadata ?? {}, "browser_metadata must be an object."),
      browser_session_id: token(argumentsPayload.browser_session_id) || undefined,
    });
    return this.persistWorkerResponse(response);
  }

  private async handleAction(argumentsPayload: Record<string, unknown>): Promise<Record<string, unknown>> {
    const browserSessionId = token(argumentsPayload.browser_session_id);
    if (!browserSessionId) {
      throw new Error("browser_session_id is required.");
    }
    const response = await this.worker.send("perform_action", {
      browser_session_id: browserSessionId,
      session_mode: token(argumentsPayload.session_mode) || undefined,
      attach_endpoint_url: token(argumentsPayload.attach_endpoint_url) || undefined,
      action: token(argumentsPayload.action),
      action_args: requireObject(argumentsPayload.action_args ?? {}, "action_args must be an object."),
      browser_metadata: requireObject(argumentsPayload.browser_metadata ?? {}, "browser_metadata must be an object."),
      checkpoint: requireObject(argumentsPayload.checkpoint ?? {}, "checkpoint must be an object."),
    });
    return this.persistWorkerResponse(response);
  }

  private async handleTakeover(argumentsPayload: Record<string, unknown>): Promise<Record<string, unknown>> {
    const browserSessionId = token(argumentsPayload.browser_session_id);
    if (!browserSessionId) {
      throw new Error("browser_session_id is required.");
    }
    const response = await this.worker.send("takeover", {
      browser_session_id: browserSessionId,
      note: token(argumentsPayload.note) || undefined,
    });
    return this.persistWorkerResponse(response);
  }

  private async handleResume(argumentsPayload: Record<string, unknown>): Promise<Record<string, unknown>> {
    const browserSessionId = token(argumentsPayload.browser_session_id);
    if (!browserSessionId) {
      throw new Error("browser_session_id is required.");
    }
    const response = await this.worker.send("resume_session", {
      browser_session_id: browserSessionId,
      session_mode: token(argumentsPayload.session_mode) || undefined,
      attach_endpoint_url: token(argumentsPayload.attach_endpoint_url) || undefined,
      checkpoint: requireObject(argumentsPayload.checkpoint ?? {}, "checkpoint must be an object."),
    });
    return this.persistWorkerResponse(response);
  }

  private async handleInterrupt(argumentsPayload: Record<string, unknown>): Promise<Record<string, unknown>> {
    const browserSessionId = token(argumentsPayload.browser_session_id);
    if (!browserSessionId) {
      throw new Error("browser_session_id is required.");
    }
    const response = await this.worker.send("interrupt_session", {
      browser_session_id: browserSessionId,
      note: token(argumentsPayload.note) || undefined,
    });
    return this.persistWorkerResponse(response);
  }

  private async persistWorkerResponse(response: Record<string, unknown>): Promise<Record<string, unknown>> {
    const workerSession =
      response.browser_session && typeof response.browser_session === "object" && !Array.isArray(response.browser_session)
        ? (response.browser_session as Record<string, unknown>)
        : undefined;
    if (!workerSession) {
      return response;
    }
    const session = await this.sessions.upsert(fromCloudSession(workerSession));
    return {
      ...response,
      browser_session: toCloudSession(session),
    };
  }
}

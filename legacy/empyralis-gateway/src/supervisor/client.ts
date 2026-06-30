import crypto from "crypto";

import { GatewayConfig } from "../config";
import {
  signSupervisorExecuteRequest,
  signSupervisorInterruptRequest,
} from "./signing";
import {
  agentComputerSystemServiceModeEnabled,
  agentComputerUserSessionBridgeEnabled,
} from "../runtime/service-mode";
import { filterCapabilitiesByDesktopPermission } from "../runtime/desktop-permissions";

interface SupervisorExecuteHttpResponse {
  success?: boolean;
  result?: Record<string, unknown>;
  error?: string;
}

interface SupervisorInterruptHttpResponse {
  success?: boolean;
  interrupted?: boolean;
  interrupt_count?: number;
  run_id?: string;
  target_request_id?: string | null;
  error?: string;
}

export interface GatewaySupervisorExecuteInput {
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
}

export interface GatewaySupervisorInterruptInput {
  requestId: string;
  runId: string;
  targetRequestId?: string;
  traceId: string;
  workspaceId: string;
  reason?: string;
}

export class GatewaySupervisorClient {
  constructor(private readonly config: Pick<GatewayConfig, "supervisorUrl" | "supervisorSecret" | "supervisorTimeoutMs">) {}

  supportedCapabilities(): string[] {
    const capabilities = [
      "filesystem.read_write",
      "shell.execute",
      "screenshot.capture",
      "computer_control.ocr",
      "computer_control.move",
      "computer_control.click",
      "computer_control.type",
      "computer_control.key",
      "computer_control.clipboard_read",
      "computer_control.clipboard_write",
      "computer_control.list_windows",
      "computer_control.list_apps",
      "computer_control.launch",
      "computer_control.launch_app",
      "computer_control.notify",
      "computer_control.applescript",
      "computer_control.speak",
    ];
    if (agentComputerSystemServiceModeEnabled() && !agentComputerUserSessionBridgeEnabled()) {
      const desktopOnly = new Set([
        "screenshot.capture",
        "computer_control.ocr",
        "computer_control.move",
        "computer_control.click",
        "computer_control.type",
        "computer_control.key",
        "computer_control.clipboard_read",
        "computer_control.clipboard_write",
        "computer_control.list_windows",
        "computer_control.list_apps",
        "computer_control.launch",
        "computer_control.launch_app",
        "computer_control.notify",
        "computer_control.applescript",
        "computer_control.speak",
      ]);
      return capabilities.filter((capability) => !desktopOnly.has(capability));
    }
    return filterCapabilitiesByDesktopPermission(capabilities);
  }

  async execute(input: GatewaySupervisorExecuteInput): Promise<Record<string, unknown>> {
    const secret = this.requiredSecret();
    const nonce = crypto.randomUUID();
    const expiresAt = new Date(Date.now() + 30_000).toISOString();
    const payload = {
      request_id: input.requestId,
      capability_id: input.capabilityId,
      run_id: input.runId,
      trace_id: input.traceId,
      workspace_id: input.workspaceId,
      arguments: input.arguments,
      runtime_access_mode: String(input.runtimeAccessMode ?? "").trim() || undefined,
      empyralis_approved: Boolean(input.empyralisApproved),
      agent_scope: String(input.agentScope ?? "").trim() || undefined,
      policy: input.policy ?? null,
      signature_version: "v2",
      nonce,
      expires_at: expiresAt,
      signature: signSupervisorExecuteRequest(secret, {
        requestId: input.requestId,
        capabilityId: input.capabilityId,
        runId: input.runId,
        traceId: input.traceId,
        workspaceId: input.workspaceId,
        arguments: input.arguments,
        runtimeAccessMode: input.runtimeAccessMode,
        empyralisApproved: input.empyralisApproved,
        agentScope: input.agentScope,
        policy: input.policy ?? null,
        nonce,
        expiresAt,
      }),
    };
    const body = await this.fetchJson<SupervisorExecuteHttpResponse>("/execute", payload);
    if (!body.success) {
      throw new Error(String(body.error || "Supervisor execute failed.").trim() || "Supervisor execute failed.");
    }
    return this.requireRecord(body.result, "Supervisor execute result payload was invalid.");
  }

  async interrupt(input: GatewaySupervisorInterruptInput): Promise<Record<string, unknown>> {
    const secret = this.requiredSecret();
    const nonce = crypto.randomUUID();
    const expiresAt = new Date(Date.now() + 30_000).toISOString();
    const payload = {
      request_id: input.requestId,
      run_id: input.runId,
      target_request_id: String(input.targetRequestId ?? "").trim() || null,
      trace_id: input.traceId,
      workspace_id: input.workspaceId,
      reason: String(input.reason ?? "").trim() || null,
      nonce,
      expires_at: expiresAt,
      signature: signSupervisorInterruptRequest(secret, {
        requestId: input.requestId,
        runId: input.runId,
        targetRequestId: input.targetRequestId,
        workspaceId: input.workspaceId,
        nonce,
        expiresAt,
      }),
    };
    const body = await this.fetchJson<SupervisorInterruptHttpResponse>("/interrupt", payload);
    if (!body.success) {
      throw new Error(String(body.error || "Supervisor interrupt failed.").trim() || "Supervisor interrupt failed.");
    }
    return this.requireRecord(body as unknown as Record<string, unknown>, "Supervisor interrupt payload was invalid.");
  }

  private requiredSecret(): string {
    const secret = String(this.config.supervisorSecret ?? "").trim();
    if (!secret) {
      throw new Error("EMPYRALIS_SUPERVISOR_SECRET is required for gateway supervisor calls.");
    }
    return secret;
  }

  private async fetchJson<T>(path: string, payload: Record<string, unknown>): Promise<T> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.config.supervisorTimeoutMs);
    try {
      const response = await fetch(`${this.config.supervisorUrl}${path}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      const body = (await response.json()) as T;
      if (!response.ok) {
        const message =
          body && typeof body === "object" && body !== null && "error" in body
            ? String((body as { error?: unknown }).error ?? "").trim()
            : "";
        throw new Error(message || `Supervisor request failed with HTTP ${response.status}.`);
      }
      return body;
    } catch (error) {
      if (error instanceof Error && error.name === "AbortError") {
        throw new Error("Supervisor request timed out.");
      }
      throw error;
    } finally {
      clearTimeout(timeout);
    }
  }

  private requireRecord(value: unknown, message: string): Record<string, unknown> {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error(message);
    }
    return value as Record<string, unknown>;
  }
}

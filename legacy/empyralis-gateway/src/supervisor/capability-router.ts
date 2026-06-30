import type {
  GatewayRequestEnvelope,
  GatewayToolInterruptPayload,
  GatewayToolInvokePayload,
} from "../protocol/types";
import { GatewayBrowserRuntime } from "../browser/runtime";
import { PersonalChannelRuntimeRegistry } from "../channels/personal-runtime";
import { ExternalAgentProxyRuntime } from "../external-agent/proxy-runtime";
import {
  agentComputerSystemServiceModeEnabled,
  agentComputerUserSessionBridgeEnabled,
} from "../runtime/service-mode";
import {
  assertCapabilityPermissionReady,
  filterCapabilitiesByDesktopPermission,
} from "../runtime/desktop-permissions";
import { GatewaySupervisorClient } from "./client";

const RUN_EXECUTOR_TTL_MS = 5 * 60 * 1000; // 5 minutes

type ExecutorName = "browser" | "external_agent_proxy" | "personal_channel" | "supervisor";

function requireObject(value: unknown, message: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(message);
  }
  return value as Record<string, unknown>;
}

function requireToken(value: unknown, label: string): string {
  const token = String(value ?? "").trim();
  if (!token) {
    throw new Error(`${label} is required.`);
  }
  return token;
}

export class GatewayCapabilityRouter {
  private readonly runExecutorMap = new Map<string, { executor: ExecutorName; cleanup: NodeJS.Timeout }>();

  constructor(
    private readonly supervisorClient: GatewaySupervisorClient,
    private readonly browserRuntime?: GatewayBrowserRuntime,
    private readonly personalChannelRuntimes = new PersonalChannelRuntimeRegistry(),
    private readonly externalAgentProxyRuntime = new ExternalAgentProxyRuntime(),
  ) {}

  supportedCapabilities(): string[] {
    const desktopBridgeReady = !agentComputerSystemServiceModeEnabled() || agentComputerUserSessionBridgeEnabled();
    return [
      ...this.supervisorClient.supportedCapabilities(),
      ...(desktopBridgeReady
        ? filterCapabilitiesByDesktopPermission(this.browserRuntime?.requestedCapabilities() ?? [])
        : []),
      ...this.personalChannelRuntimes.requestedCapabilities(),
      ...this.externalAgentProxyRuntime.requestedCapabilities(),
    ];
  }

  private trackExecutor(runId: string, executor: ExecutorName): void {
    // Clear any existing tracking for this runId
    const existing = this.runExecutorMap.get(runId);
    if (existing) {
      clearTimeout(existing.cleanup);
    }
    const cleanup = setTimeout(() => {
      this.runExecutorMap.delete(runId);
    }, RUN_EXECUTOR_TTL_MS);
    cleanup.unref?.();
    this.runExecutorMap.set(runId, { executor, cleanup });
  }

  async handleToolInvoke(
    frame: GatewayRequestEnvelope<GatewayToolInvokePayload>,
  ): Promise<Record<string, unknown>> {
    const payload = requireObject(frame.payload, "tool.invoke payload must be an object.");
    const capabilityId = requireToken(payload.capability_id, "capability_id");
    const runId = requireToken(payload.run_id, "run_id");
    const traceId = requireToken(payload.trace_id, "trace_id");
    const workspaceId = requireToken(payload.workspace_id, "workspace_id");
    const argumentsPayload = requireObject(payload.arguments ?? {}, "arguments must be an object.");
    assertCapabilityPermissionReady(capabilityId);
    if (this.browserRuntime?.supportsCapability(capabilityId)) {
      this.trackExecutor(runId, "browser");
      const result = await this.browserRuntime.handleCapabilityInvoke(
        frame as unknown as GatewayRequestEnvelope<GatewayToolInvokePayload>,
      );
      return {
        request_id: frame.id,
        capability_id: capabilityId,
        run_id: runId,
        result,
      };
    }
    if (this.externalAgentProxyRuntime.supportsCapability(capabilityId)) {
      this.trackExecutor(runId, "external_agent_proxy");
      const result = await this.externalAgentProxyRuntime.handleCapabilityInvoke(
        frame as unknown as GatewayRequestEnvelope<GatewayToolInvokePayload>,
      );
      return {
        request_id: frame.id,
        capability_id: capabilityId,
        run_id: runId,
        result,
      };
    }
    const personalRuntime = this.personalChannelRuntimes.runtimeForCapability(capabilityId);
    if (personalRuntime) {
      this.trackExecutor(runId, "personal_channel");
      const result = await personalRuntime.handleCapabilityInvoke(
        frame as unknown as GatewayRequestEnvelope<GatewayToolInvokePayload>,
      );
      return {
        request_id: frame.id,
        capability_id: capabilityId,
        run_id: runId,
        result,
      };
    }
    this.trackExecutor(runId, "supervisor");
    const result = await this.supervisorClient.execute({
      requestId: requireToken(frame.id, "request_id"),
      capabilityId,
      runId,
      traceId,
      workspaceId,
      arguments: argumentsPayload,
      runtimeAccessMode: String(payload.runtime_access_mode ?? "").trim() || undefined,
      empyralisApproved: Boolean(payload.empyralis_approved),
      agentScope: String(payload.agent_scope ?? "").trim() || undefined,
      policy: typeof payload.policy === "object" && payload.policy !== null
        ? payload.policy as Record<string, unknown>
        : null,
    });
    return {
      request_id: frame.id,
      capability_id: capabilityId,
      run_id: runId,
      result,
    };
  }

  async handleToolInterrupt(
    frame: GatewayRequestEnvelope<GatewayToolInterruptPayload>,
  ): Promise<Record<string, unknown>> {
    const payload = requireObject(frame.payload, "tool.interrupt payload must be an object.");
    const runId = requireToken(payload.run_id, "run_id");
    const traceId = requireToken(payload.trace_id, "trace_id");
    const workspaceId = requireToken(payload.workspace_id, "workspace_id");

    // Look up which executor handles this run
    const tracked = this.runExecutorMap.get(runId);
    if (!tracked) {
      return {
        interrupted: false,
        error: `No executor found for run_id "${runId}". The run may have already completed or expired.`,
        run_id: runId,
      };
    }

    const executor = tracked.executor;

    // Route to the correct executor based on the recorded executor name
    if (executor === "browser" && this.browserRuntime) {
      // Browser executor handles interrupt directly via its own mechanism
      // Route through the capability that the browser runtime registered
      const interruptResult = await this.browserRuntime.handleCapabilityInvoke(
        Object.assign({}, frame, {
          payload: {
            capability_id: "browser.session.interrupt",
            run_id: runId,
            trace_id: traceId,
            workspace_id: workspaceId,
            arguments: {
              run_id: runId,
              reason: String(payload.reason ?? "").trim() || undefined,
            },
          },
        }) as unknown as GatewayRequestEnvelope<GatewayToolInvokePayload>,
      );
      return interruptResult;
    }

    if (executor === "supervisor") {
      const interruptResult = await this.supervisorClient.interrupt({
        requestId: requireToken(frame.id, "request_id"),
        runId,
        targetRequestId: String(payload.target_request_id ?? "").trim() || undefined,
        traceId,
        workspaceId,
        reason: String(payload.reason ?? "").trim() || undefined,
      });
      return interruptResult;
    }

    // Personal channel interrupts fall through to supervisor for now
    if (executor === "personal_channel") {
      const interruptResult = await this.supervisorClient.interrupt({
        requestId: requireToken(frame.id, "request_id"),
        runId,
        targetRequestId: String(payload.target_request_id ?? "").trim() || undefined,
        traceId,
        workspaceId,
        reason: String(payload.reason ?? "").trim() || undefined,
      });
      return interruptResult;
    }

    return {
      interrupted: false,
      error: `Unknown executor "${executor}" for run_id "${runId}".`,
      run_id: runId,
    };
  }
}

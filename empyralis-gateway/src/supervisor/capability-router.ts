import type {
  GatewayRequestEnvelope,
  GatewayToolInterruptPayload,
  GatewayToolInvokePayload,
} from "../protocol/types";
import { GatewayBrowserRuntime } from "../browser/runtime";
import { GatewayShellRuntime } from "../shell/runtime";
import { GatewayLLMRuntime } from "../llm/runtime";
import { GatewayCliSetupRuntime } from "../llm/cli-setup-runtime";
import { PersonalChannelRuntimeRegistry } from "../channels/personal-runtime";
import { ExternalAgentProxyRuntime } from "../external-agent/proxy-runtime";
import { GatewaySelfUpdateRuntime } from "../update/gateway-self-update-runtime";
import { GatewayRestartRuntime } from "../update/gateway-restart-runtime";
import { GatewayDoctorRuntime } from "../health/gateway-doctor";
import {
  agentComputerSystemServiceModeEnabled,
  agentComputerUserSessionBridgeEnabled,
} from "../runtime/service-mode";
import {
  assertCapabilityPermissionReady,
  filterCapabilitiesByDesktopPermission,
} from "../runtime/desktop-permissions";
import { openClawTransportCapabilities } from "../openclaw/capabilities";
import { OpenClawProvisioningRuntime } from "../openclaw/provisioning/openclaw-provisioning-runtime";
import { OpenClawChannelSetupRuntime } from "../openclaw/provisioning/openclaw-channel-setup";

const RUN_EXECUTOR_TTL_MS = 5 * 60 * 1000; // 5 minutes

// ARCHIVED: "supervisor" executor removed (Phase U1 — product refocus).
// The Rust empyralis-supervisor daemon is no longer part of the product.
// Desktop control (mouse/keyboard/screen/fs) is OUT of scope.
// "shell_sandbox" (re-added): per-run Docker-sandboxed shell/filesystem
// execution — see src/shell/runtime.ts. Unlike the old supervisor, this has
// no unsandboxed path: it only exists where Docker (or an explicitly
// authorized full_access mode) is actually verified present.
type ExecutorName = "browser" | "external_agent_proxy" | "personal_channel" | "shell_sandbox" | "llm" | "cli_setup" | "self_update" | "doctor" | "restart" | "openclaw_provision";

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
    private readonly browserRuntime?: GatewayBrowserRuntime,
    private readonly personalChannelRuntimes = new PersonalChannelRuntimeRegistry(),
    private readonly externalAgentProxyRuntime = new ExternalAgentProxyRuntime(),
    private readonly shellRuntime?: GatewayShellRuntime,
    private readonly llmRuntime?: GatewayLLMRuntime,
    private readonly cliSetupRuntime?: GatewayCliSetupRuntime,
    // gateway.self_update is deliberately NOT gated behind a desktop
    // permission the way shell_sandbox/llm/cli_setup are above — those gate
    // on a *local capability being detected* (Docker present, Ollama
    // reachable, operator opt-in). Self-update has no such local
    // precondition: it should always be advertised so the platform can
    // always offer the "Update" button, and safety instead lives entirely
    // in the explicit member-role-gated trigger on the backend
    // (server_modules/routes_gateway.py's self-update route) plus this
    // capability's own no-op-safe / atomic-swap / rollback behavior.
    private readonly selfUpdateRuntime?: GatewaySelfUpdateRuntime,
    // gateway.doctor.run: same "always advertise, never desktop-permission-
    // gated" reasoning as self-update just above — a detect/repair/re-
    // validate pass over this computer's own health has no local-capability
    // precondition either; it should always be runnable so the fleet UI can
    // always offer the "Run doctor" action. See health/gateway-doctor.ts.
    private readonly doctorRuntime?: GatewayDoctorRuntime,
    // gateway.restart: same reasoning again — "restart this computer" has
    // no local-capability precondition, so it's always advertised. See
    // update/gateway-restart-runtime.ts.
    private readonly restartRuntime?: GatewayRestartRuntime,
    // openclaw.provision: advertised only when this box is actually an
    // OpenClaw transport box (index.ts constructs this runtime only when the
    // bridge secret is configured — the SAME condition that gates
    // setOpenClawTransportEnabled and the inbound listener, so what we say we
    // can provision and what we can actually provision are never two
    // different sets). See openclaw/provisioning/openclaw-provisioning-runtime.ts.
    private readonly openClawProvisioningRuntime?: OpenClawProvisioningRuntime,
    // openclaw.channel_setup: reads the three per-channel states (plugin
    // installed / credential present / channel enabled) and writes the ONE
    // value provisioning must never generate — the owner's own credential.
    // Gated on the same condition as openclaw.provision above, for the same
    // reason. See openclaw/provisioning/openclaw-channel-setup.ts.
    private readonly openClawChannelSetupRuntime?: OpenClawChannelSetupRuntime,
  ) {}

  supportedCapabilities(): string[] {
    const desktopBridgeReady = !agentComputerSystemServiceModeEnabled() || agentComputerUserSessionBridgeEnabled();
    return [
      ...(desktopBridgeReady
        ? filterCapabilitiesByDesktopPermission(this.browserRuntime?.requestedCapabilities() ?? [])
        : []),
      ...this.personalChannelRuntimes.requestedCapabilities(),
      // OpenClaw-transported channels have no PersonalChannelRuntime on this
      // side — their runtime is the OpenClaw process, and the gateway owns
      // only the loopback intake. Advertised only when that intake is
      // actually configured; see openclaw/capabilities.ts.
      ...openClawTransportCapabilities(),
      ...this.externalAgentProxyRuntime.requestedCapabilities(),
      // shell_sandbox capabilities are declared unconditionally, NOT gated
      // here on live Docker readiness the way browser/llm/cli_setup still
      // are below. They used to go through the same
      // filterCapabilitiesByDesktopPermission() call those do — which meant
      // that whenever Docker wasn't (yet) confirmed ready, shell.execute/
      // filesystem.read_write were dropped from this array ENTIRELY, not
      // just marked not-ready. That array becomes runtimeMetadata.
      // requestedCapabilities (index.ts), which is the SAME array that
      // seeds capability_readiness.requested in every heartbeat
      // (health/service-inventory.ts's buildFastPassiveInventorySnapshot/
      // collectPassiveInventorySnapshot map permission_states off exactly
      // that array) — so a capability missing here was permanently invisible
      // to permission_states too, not just "restricted": the backend's
      // gateway_capability_not_ready check (server_modules/
      // gateway_execution_service.py) had no key to find at all, no matter
      // how quickly Docker itself came up. Real dispatch-time safety is
      // unaffected: handleToolInvoke() below still calls
      // assertCapabilityPermissionReady() before running anything, which
      // independently throws blocked/local_permission_denied while Docker
      // isn't ready (see runtime/desktop-permissions.ts, exercised by
      // desktop-permissions-shell-sandbox.test.ts). Advertising the
      // capability and being allowed to run it are separate questions;
      // capability_readiness.permission_states is what's supposed to answer
      // "ready or why not" for the *advertised* set, and it can only do that
      // if the capability is actually IN that set.
      ...(this.shellRuntime?.requestedCapabilities() ?? []),
      // llm.generate is gated on the "llm_runtime" permission, only "granted"
      // when a local Ollama endpoint reads ready — same pattern as shell/Docker
      // (BYO-brain Phase 2).
      ...filterCapabilitiesByDesktopPermission(this.llmRuntime?.requestedCapabilities() ?? []),
      // cli.install/cli.login.* are gated on the "cli_setup" permission —
      // granted only once the box operator has explicitly opted in (Build F).
      ...filterCapabilitiesByDesktopPermission(this.cliSetupRuntime?.requestedCapabilities() ?? []),
      ...(this.selfUpdateRuntime?.requestedCapabilities() ?? []),
      ...(this.doctorRuntime?.requestedCapabilities() ?? []),
      ...(this.restartRuntime?.requestedCapabilities() ?? []),
      ...(this.openClawProvisioningRuntime?.requestedCapabilities() ?? []),
      ...(this.openClawChannelSetupRuntime?.requestedCapabilities() ?? []),
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
    if (this.shellRuntime?.supportsCapability(capabilityId)) {
      this.trackExecutor(runId, "shell_sandbox");
      const result = await this.shellRuntime.handleCapabilityInvoke(
        frame as unknown as GatewayRequestEnvelope<GatewayToolInvokePayload>,
      );
      return {
        request_id: frame.id,
        capability_id: capabilityId,
        run_id: runId,
        result,
      };
    }
    if (this.llmRuntime?.supportsCapability(capabilityId)) {
      this.trackExecutor(runId, "llm");
      const result = await this.llmRuntime.handleCapabilityInvoke(
        frame as unknown as GatewayRequestEnvelope<GatewayToolInvokePayload>,
      );
      return {
        request_id: frame.id,
        capability_id: capabilityId,
        run_id: runId,
        result,
      };
    }
    if (this.cliSetupRuntime?.supportsCapability(capabilityId)) {
      this.trackExecutor(runId, "cli_setup");
      const result = await this.cliSetupRuntime.handleCapabilityInvoke(
        frame as unknown as GatewayRequestEnvelope<GatewayToolInvokePayload>,
      );
      return {
        request_id: frame.id,
        capability_id: capabilityId,
        run_id: runId,
        result,
      };
    }
    if (this.selfUpdateRuntime?.supportsCapability(capabilityId)) {
      this.trackExecutor(runId, "self_update");
      const result = await this.selfUpdateRuntime.handleCapabilityInvoke(
        frame as unknown as GatewayRequestEnvelope<GatewayToolInvokePayload>,
      );
      return {
        request_id: frame.id,
        capability_id: capabilityId,
        run_id: runId,
        result,
      };
    }
    if (this.doctorRuntime?.supportsCapability(capabilityId)) {
      this.trackExecutor(runId, "doctor");
      const result = await this.doctorRuntime.handleCapabilityInvoke(
        frame as unknown as GatewayRequestEnvelope<GatewayToolInvokePayload>,
      );
      return {
        request_id: frame.id,
        capability_id: capabilityId,
        run_id: runId,
        result,
      };
    }
    if (this.restartRuntime?.supportsCapability(capabilityId)) {
      this.trackExecutor(runId, "restart");
      const result = await this.restartRuntime.handleCapabilityInvoke(
        frame as unknown as GatewayRequestEnvelope<GatewayToolInvokePayload>,
      );
      return {
        request_id: frame.id,
        capability_id: capabilityId,
        run_id: runId,
        result,
      };
    }
    if (this.openClawProvisioningRuntime?.supportsCapability(capabilityId)) {
      this.trackExecutor(runId, "openclaw_provision");
      const result = await this.openClawProvisioningRuntime.handleCapabilityInvoke(
        frame as unknown as GatewayRequestEnvelope<GatewayToolInvokePayload>,
      );
      return {
        request_id: frame.id,
        capability_id: capabilityId,
        run_id: runId,
        result,
      };
    }
    if (this.openClawChannelSetupRuntime?.supportsCapability(capabilityId)) {
      this.trackExecutor(runId, "openclaw_provision");
      const result = await this.openClawChannelSetupRuntime.handleCapabilityInvoke(
        frame as unknown as GatewayRequestEnvelope<GatewayToolInvokePayload>,
      );
      return {
        request_id: frame.id,
        capability_id: capabilityId,
        run_id: runId,
        result,
      };
    }
    // ARCHIVED (Phase U1): supervisor executor removed.
    // Capabilities that don't match browser, external-agent-proxy, personal-channel,
    // or shell_sandbox are no longer supported. Desktop control (mouse/keyboard/
    // screen) is still OUT — only shell/filesystem came back, and only sandboxed.
    throw new Error(
      `No executor available for capability "${capabilityId}". ` +
      `Supported executors: browser, external_agent_proxy, personal_channel, shell_sandbox, llm, cli_setup, self_update, doctor, restart. ` +
      `Desktop control capabilities are not part of the Empyralis product.`,
    );
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

    if (executor === "personal_channel") {
      // Personal channel interrupts are routed through the channel's own runtime
      return {
        interrupted: false,
        error: `Personal channel interrupt not yet implemented for run_id "${runId}".`,
        run_id: runId,
      };
    }

    if (executor === "shell_sandbox") {
      // shell_sandbox runs are a single awaited call per container (bounded
      // by its own timeout) rather than a long-lived session — there is no
      // separate in-flight handle to signal yet. A real implementation would
      // track run_id -> container name and issue `docker kill`; not built.
      return {
        interrupted: false,
        error: `shell_sandbox interrupt not yet implemented for run_id "${runId}".`,
        run_id: runId,
      };
    }

    if (executor === "self_update") {
      // gateway.self_update is a single awaited call, deliberately never
      // interruptible mid-flight: once the download/extract has started,
      // cancelling partway would leave a half-staged release dir — the
      // runtime's own atomicity (stage under a temp name, rename only once
      // valid) is what protects against that, not tool.interrupt. Safe to
      // just report "not applicable" rather than build a cancel path that
      // would only ever be able to interrupt the download, not the parts
      // that actually matter.
      return {
        interrupted: false,
        error: `gateway.self_update interrupt not applicable for run_id "${runId}" — the update either completes or fails atomically on its own.`,
        run_id: runId,
      };
    }

    if (executor === "llm") {
      // llm.generate is a single awaited request/response to the local Ollama
      // endpoint, bounded by its own timeout — there is no long-lived session
      // handle to signal. The in-flight fetch is abandoned when its own
      // AbortController timeout fires; there is nothing to cancel out-of-band.
      return {
        interrupted: false,
        error: `llm interrupt not applicable for run_id "${runId}" (single bounded request).`,
        run_id: runId,
      };
    }

    if (executor === "doctor") {
      // gateway.doctor.run is a single bounded pass over a fixed, small set
      // of checks (each with its own fast probe/cache) — there is no
      // long-lived session handle to cancel out-of-band, same reasoning as
      // "llm" above.
      return {
        interrupted: false,
        error: `gateway.doctor.run interrupt not applicable for run_id "${runId}" (a bounded set of checks that completes quickly).`,
        run_id: runId,
      };
    }

    if (executor === "restart") {
      // gateway.restart is a single awaited call, deliberately never
      // interruptible mid-flight — same reasoning as "self_update" above:
      // once the handoff has been spawned (or the supervised shutdown
      // scheduled), cancelling would only ever be able to stop the
      // scheduled shutdown, not the handoff process that's already
      // detached and running on its own.
      return {
        interrupted: false,
        error: `gateway.restart interrupt not applicable for run_id "${runId}" — the restart either completes or fails on its own.`,
        run_id: runId,
      };
    }

    if (executor === "cli_setup" && this.cliSetupRuntime) {
      // The one executor here that IS a genuine long-lived session — a
      // cli.login.start held open across a multi-minute human round trip.
      // tool.interrupt is how the control plane cancels it early (the user
      // gave up, or a differentiated timeout upstream fired first).
      return this.cliSetupRuntime.interruptRun(runId);
    }

    // ARCHIVED (Phase U1): supervisor executor removed.
    return {
      interrupted: false,
      error: `Unknown executor "${executor}" for run_id "${runId}".`,
      run_id: runId,
    };
  }
}

import type { GatewayRequestEnvelope, GatewayToolInvokePayload } from "../protocol/types";
import { installCliSubscriptionRuntime, CliInstallError, type CliInstallRuntime } from "./cli-installer";
import { CliLoginSessionManager, CliLoginError, type CliLoginEventPublisher, type CliLoginInputKind, type CliLoginMethod, type CliLoginRuntime } from "./cli-login-session";
import { invalidatePassiveInventoryCache } from "../health/service-inventory";

// BYO-brain onboarding (Build F): the real install + sign-in plumbing behind
// what was, until this build, copy-paste guidance only. Sibling to
// GatewayLLMRuntime on the capability router — same executor shape
// (requestedCapabilities/supportsCapability/handleCapabilityInvoke) — but a
// distinct concern: this runs SETUP actions (install a CLI, drive its login),
// llm.generate runs the CLI once it's already ready.

export const CLI_INSTALL_CAPABILITY = "cli.install";
export const CLI_LOGIN_START_CAPABILITY = "cli.login.start";
export const CLI_LOGIN_INPUT_CAPABILITY = "cli.login.input";

const SUPPORTED_CAPABILITIES = [CLI_INSTALL_CAPABILITY, CLI_LOGIN_START_CAPABILITY, CLI_LOGIN_INPUT_CAPABILITY];

const RUNTIMES = new Set(["claude_code", "codex"]);
const DEFAULT_INSTALL_TIMEOUT_MS = 180_000;

function requireObject(value: unknown, message: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(message);
  }
  return value as Record<string, unknown>;
}

function token(value: unknown): string {
  return String(value ?? "").trim();
}

function requireRuntime(value: unknown): CliInstallRuntime & CliLoginRuntime {
  const runtime = token(value);
  if (!RUNTIMES.has(runtime)) {
    throw new Error(`Unsupported cli_setup runtime "${runtime || "unknown"}" (expected "claude_code" or "codex").`);
  }
  return runtime as CliInstallRuntime & CliLoginRuntime;
}

/** Maps a typed install/login failure into a precise, honest message — same
 *  role as llm/runtime.ts's cliErrorMessage, for the control plane's
 *  platform-voice error mapper to pattern-match on. */
function setupErrorMessage(action: "install" | "login", runtime: string, error: unknown): string {
  const label = runtime === "claude_code" ? "Claude Code" : "Codex";
  if (error instanceof CliInstallError) {
    return `${label} install failed on this Gateway (${error.kind}): ${error.message}`;
  }
  if (error instanceof CliLoginError) {
    return `${label} sign-in failed on this Gateway (${error.kind}): ${error.message}`;
  }
  const reason = error instanceof Error ? error.message : String(error);
  return `${label} ${action} failed on this Gateway (${reason}).`;
}

export interface GatewayCliSetupRuntimeConfig {
  installTimeoutMs?: number;
  /** Injectable for tests. */
  installer?: typeof installCliSubscriptionRuntime;
  loginSessions?: CliLoginSessionManager;
}

export class GatewayCliSetupRuntime {
  private readonly installTimeoutMs: number;
  private readonly installer: typeof installCliSubscriptionRuntime;
  private readonly loginSessions: CliLoginSessionManager;

  constructor(config: GatewayCliSetupRuntimeConfig = {}) {
    this.installTimeoutMs = config.installTimeoutMs || DEFAULT_INSTALL_TIMEOUT_MS;
    this.installer = config.installer ?? installCliSubscriptionRuntime;
    this.loginSessions = config.loginSessions ?? new CliLoginSessionManager();
  }

  /** Set once, after the ws-client exists — see index.ts. Login sessions
   *  push their URL/code/done events through this, out of band from the
   *  cli.login.start request/response itself. */
  setEventPublisher(publisher: CliLoginEventPublisher): void {
    // Wrap the caller's publisher so a successful "done" event ALSO
    // invalidates the passive-inventory cache — that flip authenticates
    // the runtime on disk, and the UI must see .authenticated: true on
    // the very next heartbeat, not up to 60 seconds later.
    this.loginSessions.setEventPublisher(async (event) => {
      try {
        await publisher(event);
      } finally {
        if (event.event === "done" && event.ok) {
          invalidatePassiveInventoryCache();
        }
      }
    });
  }

  requestedCapabilities(): string[] {
    return [...SUPPORTED_CAPABILITIES];
  }

  supportsCapability(capabilityId: string): boolean {
    return SUPPORTED_CAPABILITIES.includes(token(capabilityId));
  }

  /** Whether this run_id belongs to an in-flight login session — used by the
   *  capability router to route tool.interrupt here. */
  hasInterruptibleRun(runId: string): boolean {
    return this.loginSessions.hasSession(runId);
  }

  async interruptRun(runId: string): Promise<Record<string, unknown>> {
    const result = await this.loginSessions.cancel(runId);
    return { interrupted: result.ok, run_id: runId };
  }

  async handleCapabilityInvoke(
    frame: GatewayRequestEnvelope<GatewayToolInvokePayload>,
  ): Promise<Record<string, unknown>> {
    const payload = frame.payload;
    const capabilityId = token(payload.capability_id);
    const args = requireObject(payload.arguments ?? {}, "arguments must be an object.");
    const runId = token(payload.run_id);

    if (capabilityId === CLI_INSTALL_CAPABILITY) {
      const runtime = requireRuntime(args.runtime);
      try {
        const result = await this.installer({ runtime, timeoutMs: this.installTimeoutMs });
        // The service-inventory cache would otherwise report "not
        // installed" for up to 60 more seconds — the UI's row would sit
        // on the stale value until the TTL expired plus one heartbeat.
        invalidatePassiveInventoryCache();
        return { ...result };
      } catch (error) {
        throw new Error(setupErrorMessage("install", runtime, error));
      }
    }

    if (capabilityId === CLI_LOGIN_START_CAPABILITY) {
      const runtime = requireRuntime(args.runtime);
      if (!runId) {
        throw new Error("cli.login.start requires run_id.");
      }
      const rawMethod = token(args.method);
      const method = rawMethod ? (rawMethod as CliLoginMethod) : undefined;
      try {
        return await this.loginSessions.start({ runId, runtime, method });
      } catch (error) {
        throw new Error(setupErrorMessage("login", runtime, error));
      }
    }

    if (capabilityId === CLI_LOGIN_INPUT_CAPABILITY) {
      if (!runId) {
        throw new Error("cli.login.input requires run_id.");
      }
      // Legacy shape (Build F): {code: "…"} → treated as kind="code".
      // Extended shape (multi-method BYO-brain): {kind: "api_key" |
      // "access_token" | "code", value: "…"} — the kind chooses which
      // stdin-write path to use in the session manager.
      const legacyCode = token(args.code);
      const explicitKind = token(args.kind);
      const explicitValue = token(args.value);
      const value = explicitValue || legacyCode;
      const kind = (explicitKind || "code") as CliLoginInputKind;
      try {
        return await this.loginSessions.input({ runId, value, kind });
      } catch (error) {
        throw new Error(setupErrorMessage("login", "claude_code", error));
      }
    }

    throw new Error(`Unsupported cli_setup capability: ${capabilityId || "unknown"}`);
  }
}

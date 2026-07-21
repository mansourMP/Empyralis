import type { GatewayRequestEnvelope, GatewayToolInvokePayload } from "../protocol/types";
import type { GatewayCheckpoints, GatewayHealthState } from "../state/checkpoints";
import type { PersonalChannelRuntimeRegistry } from "../channels/personal-runtime";
import {
  collectPassiveInventorySnapshot,
  invalidatePassiveInventoryCache,
  type PassiveInventorySnapshot,
} from "./service-inventory";
import { detectGatewaySupervisor, type GatewaySupervisorMode } from "../update/gateway-restart-handoff";

/**
 * In-gateway "doctor": detect -> (safe) repair -> re-validate, exposed as the
 * `gateway.doctor.run` capability (wired into GatewayCapabilityRouter the
 * same way gateway.self_update and channel.imessage.personal.recheck are —
 * see supervisor/capability-router.ts's constructor and handleToolInvoke()).
 *
 * This is a first, real check/repair set (see docs/design/reliability-audit-1
 * -gateway-health.md §7 for what was missing before this: no doctor of any
 * kind existed in the gateway process itself, only a read-only backend HTTP
 * endpoint aggregating stored state). Modeled on OpenClaw's own doctor
 * contract (read-only reference, /Users/mansur/openclaw/src/flows/health-
 * checks.ts's `{id, kind, description, detect, repair?}` shape and doctor-
 * repair-flow.ts's re-run-detect-after-repair validation) but written fresh
 * for this codebase — nothing imported or copied.
 *
 * Every check is a plain `{id, label, detect, repair?}` object pushed into
 * `buildDefaultGatewayDoctorChecks()`'s array — adding a new check later
 * means adding one more object to that array, never touching
 * capability-router.ts or this module's dispatch plumbing.
 *
 * Repairs are conservative by design: a check only gets a `repair()` when
 * there is something genuinely safe and idempotent to do without a human in
 * the loop (today: only invalidating the passive-inventory cache so a
 * capability that just became available is picked up immediately instead of
 * waiting out the cache TTL). Everything else is honest report-only — a
 * doctor that cannot fix something must say so clearly, never guess or take
 * a risky action (installing a systemd unit, granting macOS Full Disk
 * Access, running `brew install` unattended) just to claim a green check.
 */

export type GatewayDoctorCheckStatus = "pass" | "warn" | "fail" | "skip";

export interface GatewayDoctorDetectOutcome {
  status: GatewayDoctorCheckStatus;
  /** One plain-language sentence — no capability ids, no stack traces, no
   *  developer jargon. This is what renders directly in the fleet UI. */
  detail: string;
}

export interface GatewayDoctorRepairOutcome {
  /** Plain-language note on what the repair attempt did, shown alongside the
   *  re-validated result regardless of whether it actually cleared the
   *  finding. */
  detail?: string;
}

/** Narrow, test-friendly view of PersonalChannelRuntimeRegistry — only the
 *  one method this module needs, so a unit test can hand in a fake without
 *  constructing a real registry + its channel runtimes. */
export interface GatewayDoctorPersonalChannelRegistry {
  runtimeForChannel: PersonalChannelRuntimeRegistry["runtimeForChannel"];
}

export interface GatewayDoctorContext {
  /** The gateway's own last-recorded connection health, synchronously
   *  available via GatewayCheckpoints.currentHealthState() — the SAME
   *  in-memory value cloud/ws-client.ts's sendHeartbeat() now threads into
   *  the heartbeat payload's `health_state` field instead of the literal
   *  "online" the reliability audit flagged (heartbeat-payload.ts). Reading
   *  it here, synchronously, means this check can never race a debounced
   *  checkpoints.json disk write the way an async checkpoints.load() would. */
  getHealthState: () => GatewayHealthState;
  /** The SAME fixed capability list this process advertised at startup
   *  (GatewayCapabilityRouter.supportedCapabilities(), computed once — see
   *  index.ts's documented reason why). Read lazily via a getter rather than
   *  captured once, since GatewayDoctorRuntime is constructed before the
   *  router that owns this list exists (same two-phase-wiring shape index.ts
   *  already uses for triggerShutdown/selfUpdateRuntime). */
  getRequestedCapabilities: () => string[];
  personalChannelRuntimes?: GatewayDoctorPersonalChannelRegistry;
  env: NodeJS.ProcessEnv;
  platform: NodeJS.Platform;
  collectPassiveInventory: typeof collectPassiveInventorySnapshot;
  invalidatePassiveInventoryCache: typeof invalidatePassiveInventoryCache;
  detectSupervisor: (env: NodeJS.ProcessEnv, platform: NodeJS.Platform) => GatewaySupervisorMode;
}

export interface GatewayDoctorCheck {
  id: string;
  /** Plain-language check name shown in the fleet UI (never a dev-jargon id
   *  like "capability_readiness" or "cli_subscription"). */
  label: string;
  detect: (ctx: GatewayDoctorContext) => Promise<GatewayDoctorDetectOutcome>;
  /** Present only for checks with a safe, idempotent fix. Absent means
   *  report-only by construction — runGatewayDoctor() never calls a repair
   *  that doesn't exist. */
  repair?: (ctx: GatewayDoctorContext) => Promise<GatewayDoctorRepairOutcome>;
}

export interface GatewayDoctorCheckResult {
  id: string;
  label: string;
  status: GatewayDoctorCheckStatus;
  detail: string;
  repairable: boolean;
  /** True only once repair() ran AND the re-validated detect() afterward
   *  came back "pass" — never set from the repair step's own optimistic
   *  claim. */
  repaired?: boolean;
  repair_detail?: string;
}

export interface GatewayDoctorRunResult {
  checked_at: string;
  repair_requested: boolean;
  results: GatewayDoctorCheckResult[];
  summary: Record<string, number>;
}

// ---------------------------------------------------------------------------
// Plain-language labels for the underlying desktop-permission ids that gate
// capability readiness (runtime/desktop-permissions.ts's DesktopPermissionId)
// — used only to translate a blocked capability into words a non-developer
// founder/operator can read, never surfaced as raw ids.
const PERMISSION_PLAIN_LABEL: Record<string, string> = {
  shell_sandbox: "sandboxed shell access",
  llm_runtime: "local AI models",
  cli_setup: "AI sign-in tools",
  browser: "browser control",
  screen_recording: "screen recording",
  accessibility: "accessibility control",
  clipboard: "clipboard access",
  automation: "app automation",
};

function summarizeBlockedCapabilities(capabilityReadiness: PassiveInventorySnapshot["capability_readiness"]): string {
  const labels = new Set<string>();
  for (const capabilityId of capabilityReadiness.blocked) {
    const status = capabilityReadiness.permission_states[capabilityId];
    const label = status ? PERMISSION_PLAIN_LABEL[status.permission] : undefined;
    labels.add(label || capabilityId);
  }
  return Array.from(labels).join(", ");
}

// ---------------------------------------------------------------------------
// Check 1: cloud connection. Reads the SAME real, locally-tracked health
// state (state/checkpoints.ts's GatewayHealthState, written throughout
// cloud/ws-client.ts) that the heartbeat payload does NOT transmit — see
// heartbeat-payload.ts:14, which sends the literal "online" unconditionally.
// This is the honest signal the audit flagged as missing from anything
// visible outside the process; the doctor is the first thing to surface it.
const CLOUD_CONNECTION_CHECK: GatewayDoctorCheck = {
  id: "cloud_connection",
  label: "Connection to Empyralis",
  async detect(ctx) {
    const state = ctx.getHealthState();
    if (state === "online") {
      return { status: "pass", detail: "This computer is connected to Empyralis." };
    }
    if (state === "reconnecting") {
      return {
        status: "warn",
        detail: "This computer is reconnecting to Empyralis right now. It should recover on its own within a few seconds.",
      };
    }
    if (state === "degraded") {
      return {
        status: "warn",
        detail: "The connection to Empyralis is degraded — some updates from this computer may be delayed.",
      };
    }
    if (state === "offline") {
      return { status: "fail", detail: "This computer is not connected to Empyralis right now." };
    }
    return { status: "warn", detail: "This computer hasn't reported a connection state yet." };
  },
  // No repair: reconnect is already automatic (cloud/reconnect.ts's own
  // exponential-backoff loop). There is nothing safe for a capability
  // dispatched over that same connection to do differently than the loop
  // already does on its own — see docs/design/reliability-audit-1-gateway-
  // health.md §1 for the existing reconnect behavior this doesn't duplicate.
};

// ---------------------------------------------------------------------------
// Check 2: capability readiness. Reuses the exact same passive-inventory
// probe/cache the heartbeat loop already runs (health/service-inventory.ts)
// against the fixed capability list this process advertised at startup.
const CAPABILITY_READINESS_CHECK: GatewayDoctorCheck = {
  id: "capability_readiness",
  label: "Feature readiness",
  async detect(ctx) {
    const requested = ctx.getRequestedCapabilities();
    if (requested.length === 0) {
      return { status: "pass", detail: "No optional features are enabled on this computer." };
    }
    const snapshot = await ctx.collectPassiveInventory({ requestedCapabilities: requested });
    const { blocked, ready } = snapshot.capability_readiness;
    if (blocked.length === 0) {
      return { status: "pass", detail: `All ${ready.length} enabled feature(s) on this computer are ready.` };
    }
    const names = summarizeBlockedCapabilities(snapshot.capability_readiness);
    return {
      status: "warn",
      detail: `${blocked.length} of ${requested.length} enabled feature(s) aren't ready yet${names ? `: ${names}` : ""}.`,
    };
  },
  async repair(ctx) {
    // Safe + idempotent: this doesn't fix anything about the underlying
    // feature — it just forces a fresh probe instead of serving up to 60
    // stale seconds of cache (service-inventory.ts's
    // PASSIVE_INVENTORY_CACHE_TTL_MS). Fixes the common "I just installed
    // Docker / signed into Claude Code and it still says blocked" case with
    // zero risk of making anything worse.
    ctx.invalidatePassiveInventoryCache();
    await ctx.collectPassiveInventory({ requestedCapabilities: ctx.getRequestedCapabilities() });
    return { detail: "Refreshed the on-box feature check instead of using a cached result." };
  },
};

// ---------------------------------------------------------------------------
// Check 3: CLI subscription readiness (Claude Code / Codex). Reuses the same
// passive probes (probeClaudeCli/probeCodexCli inside collectPassiveInventory
// Snapshot) that already feed the Capabilities tab — no separate probe.
function cliStatusPhrase(status: string | undefined, label: string): string {
  switch (status) {
    case "ready":
      return `${label} is ready to use`;
    case "degraded":
      return `${label} is installed but not signed in`;
    case "missing":
    case "offline":
      return `${label} isn't installed`;
    default:
      return `${label}'s status is unknown`;
  }
}

const CLI_SUBSCRIPTION_CHECK: GatewayDoctorCheck = {
  id: "cli_subscription",
  label: "AI CLI sign-in",
  async detect(ctx) {
    const snapshot = await ctx.collectPassiveInventory({ requestedCapabilities: ctx.getRequestedCapabilities() });
    const byId = new Map(snapshot.service_inventory.map((item) => [item.id, item.status]));
    const claudeStatus = byId.get("claude_cli");
    const codexStatus = byId.get("codex_cli");
    if (claudeStatus === undefined && codexStatus === undefined) {
      return { status: "skip", detail: "AI CLI sign-in isn't tracked on this computer." };
    }
    if (claudeStatus === "ready" || codexStatus === "ready") {
      return { status: "pass", detail: "At least one AI CLI is installed and signed in on this computer." };
    }
    const parts = [
      claudeStatus !== undefined ? cliStatusPhrase(claudeStatus, "Claude Code") : null,
      codexStatus !== undefined ? cliStatusPhrase(codexStatus, "Codex") : null,
    ].filter((part): part is string => Boolean(part));
    return {
      status: "warn",
      detail: `${parts.join("; ")}. Sign in from the Capabilities tab to use a subscription-based AI CLI.`,
    };
  },
  // No repair: installing/signing in is an interactive flow (the existing
  // cli.install / cli.login.* capabilities, driven from the Capabilities
  // tab's own UI) — not something a doctor pass should trigger unattended.
};

// ---------------------------------------------------------------------------
// Check 4: personal-channel / iMessage readiness. Reuses the exact same
// staged probe (bridges/imsg-imessage-client.ts's probeImsgIMessageStaged)
// that ImsgIMessagePersonalChannelRuntime.getHealthSnapshot() already runs
// for the heartbeat and for the setup panel's "Re-check" button — going
// through getHealthSnapshot() rather than reaching around it keeps this
// doctor check and the setup panel reporting identically, from one code
// path, instead of a second copy of the staged-probe interpretation logic.
function imsgIssuePhrase(issue: string | undefined): string {
  if (!issue) {
    return "iMessage isn't reachable right now.";
  }
  if (issue.endsWith("_imsg_not_installed")) {
    return "The iMessage helper (imsg) isn't installed on this computer yet.";
  }
  if (issue.endsWith("_imsg_rpc_unsupported")) {
    return "The installed iMessage helper is too old to work with this computer.";
  }
  if (issue.endsWith("_full_disk_access_required")) {
    return "This computer needs Full Disk Access granted to Messages — open System Settings > Privacy & Security > Full Disk Access and enable it.";
  }
  return "iMessage isn't reachable right now.";
}

const PERSONAL_CHANNEL_IMESSAGE_CHECK: GatewayDoctorCheck = {
  id: "personal_channel_imessage",
  label: "iMessage",
  async detect(ctx) {
    const runtime = ctx.personalChannelRuntimes?.runtimeForChannel("imessage_personal");
    if (!runtime) {
      return { status: "skip", detail: "iMessage isn't configured on this computer." };
    }
    const snapshot = await runtime.getHealthSnapshot?.();
    if (!snapshot) {
      return { status: "warn", detail: "iMessage status couldn't be read." };
    }
    if (snapshot.connected) {
      return { status: "pass", detail: "iMessage is connected and ready." };
    }
    return { status: "fail", detail: imsgIssuePhrase(snapshot.issues?.[0]) };
  },
  // No repair here: an install action already exists as its own explicit,
  // user-triggered capability (channel.imessage.personal.install ->
  // runImsgHomebrewInstall(), channels/imsg-imessage-runtime.ts:163-171) with
  // its own long timeout (up to 280s, personal_channels_service.py) — running
  // a Homebrew install unattended inside a doctor pass would make an already
  // slow, occasionally-interactive step invisible and unbounded. Full Disk
  // Access cannot be granted programmatically at all (macOS System Settings,
  // by design). Report-only, with the exact fix in plain language above.
};

// ---------------------------------------------------------------------------
// Check 5: supervisor presence. Reuses detectGatewaySupervisor() unchanged —
// the same function gateway-self-update-runtime.ts already calls to decide
// whether a self-update can rely on Restart=always or must spawn its own
// restart handoff (see update/gateway-restart-handoff.ts).
const SUPERVISOR_PRESENCE_CHECK: GatewayDoctorCheck = {
  id: "supervisor_presence",
  label: "Automatic restart",
  async detect(ctx) {
    const mode = ctx.detectSupervisor(ctx.env, ctx.platform);
    if (mode === "systemd" || mode === "launchd") {
      return {
        status: "pass",
        detail: `This computer will automatically restart itself if it crashes (managed by ${mode}).`,
      };
    }
    return {
      status: "warn",
      detail: "This computer has no automatic restart set up — if it crashes, it stays down until someone starts it again.",
    };
  },
  // No repair: installing/enabling a systemd unit or launchd job needs root
  // and a re-run of the installer script (scripts/install-agent-computer.sh)
  // — not something an unprivileged capability invoke inside the already-
  // running gateway process can safely do to itself.
};

export function buildDefaultGatewayDoctorChecks(): GatewayDoctorCheck[] {
  return [
    CLOUD_CONNECTION_CHECK,
    CAPABILITY_READINESS_CHECK,
    CLI_SUBSCRIPTION_CHECK,
    PERSONAL_CHANNEL_IMESSAGE_CHECK,
    SUPERVISOR_PRESENCE_CHECK,
  ];
}

export interface GatewayDoctorRunOptions {
  repair?: boolean;
}

/** detect -> (safe) repair -> re-validate, run over every check in order.
 *  Never throws: a check whose detect()/repair() itself throws is reported
 *  as a failed result for that one check rather than aborting the whole
 *  run — one broken probe must never hide every other check's result. */
export async function runGatewayDoctor(
  checks: GatewayDoctorCheck[],
  ctx: GatewayDoctorContext,
  options: GatewayDoctorRunOptions = {},
): Promise<GatewayDoctorRunResult> {
  const repairRequested = Boolean(options.repair);
  const results: GatewayDoctorCheckResult[] = [];

  for (const check of checks) {
    const outcome = await safeDetect(check, ctx);
    const result: GatewayDoctorCheckResult = {
      id: check.id,
      label: check.label,
      status: outcome.status,
      detail: outcome.detail,
      repairable: Boolean(check.repair),
    };

    const needsRepair = repairRequested && check.repair && (outcome.status === "fail" || outcome.status === "warn");
    if (needsRepair && check.repair) {
      try {
        const repairOutcome = await check.repair(ctx);
        result.repair_detail = repairOutcome.detail;
      } catch (error) {
        result.repair_detail = `Repair attempt failed: ${error instanceof Error ? error.message : String(error)}`;
      }
      // Re-validate regardless of whether the repair call itself threw —
      // never trust the repair step's own claim, always re-run detect (same
      // contract OpenClaw's doctor-repair-flow.ts uses: re-run detect in a
      // scoped validation pass after each repair to confirm it actually
      // cleared the finding).
      const revalidated = await safeDetect(check, ctx);
      result.status = revalidated.status;
      result.detail = revalidated.detail;
      result.repaired = revalidated.status === "pass";
    }

    results.push(result);
  }

  const summary: Record<string, number> = {};
  for (const result of results) {
    summary[result.status] = (summary[result.status] ?? 0) + 1;
    if (result.repaired) {
      summary.repaired = (summary.repaired ?? 0) + 1;
    }
  }

  return {
    checked_at: new Date().toISOString(),
    repair_requested: repairRequested,
    results,
    summary,
  };
}

async function safeDetect(check: GatewayDoctorCheck, ctx: GatewayDoctorContext): Promise<GatewayDoctorDetectOutcome> {
  try {
    return await check.detect(ctx);
  } catch (error) {
    return {
      status: "fail",
      detail: `Couldn't check this — ${error instanceof Error ? error.message : String(error)}.`,
    };
  }
}

// ---------------------------------------------------------------------------
// Capability wiring — same shape as GatewaySelfUpdateRuntime (update/gateway-
// self-update-runtime.ts): requestedCapabilities()/supportsCapability()/
// handleCapabilityInvoke(), constructed in index.ts and passed into
// GatewayCapabilityRouter's constructor, dispatched by
// GatewayCapabilityRouter.handleToolInvoke() the same way every other
// executor is.
export const GATEWAY_DOCTOR_CAPABILITY = "gateway.doctor.run";

export interface GatewayDoctorRuntimeOptions {
  checkpoints: Pick<GatewayCheckpoints, "currentHealthState">;
  /** See GatewayDoctorContext.getRequestedCapabilities' doc comment for why
   *  this is a getter, not a plain array — capabilityRouter.
   *  supportedCapabilities() doesn't exist yet at the point this runtime is
   *  constructed in index.ts. */
  getRequestedCapabilities: () => string[];
  personalChannelRuntimes?: GatewayDoctorPersonalChannelRegistry;
  env?: NodeJS.ProcessEnv;
  platform?: NodeJS.Platform;
  /** Injectable for tests. */
  collectPassiveInventory?: typeof collectPassiveInventorySnapshot;
  invalidatePassiveInventoryCache?: typeof invalidatePassiveInventoryCache;
  detectSupervisor?: (env: NodeJS.ProcessEnv, platform: NodeJS.Platform) => GatewaySupervisorMode;
  checks?: GatewayDoctorCheck[];
}

export class GatewayDoctorRuntime {
  private readonly checks: GatewayDoctorCheck[];
  private readonly ctx: GatewayDoctorContext;

  constructor(options: GatewayDoctorRuntimeOptions) {
    this.checks = options.checks ?? buildDefaultGatewayDoctorChecks();
    this.ctx = {
      getHealthState: () => options.checkpoints.currentHealthState(),
      getRequestedCapabilities: options.getRequestedCapabilities,
      personalChannelRuntimes: options.personalChannelRuntimes,
      env: options.env ?? process.env,
      platform: options.platform ?? process.platform,
      collectPassiveInventory: options.collectPassiveInventory ?? collectPassiveInventorySnapshot,
      invalidatePassiveInventoryCache: options.invalidatePassiveInventoryCache ?? invalidatePassiveInventoryCache,
      detectSupervisor: options.detectSupervisor ?? detectGatewaySupervisor,
    };
  }

  requestedCapabilities(): string[] {
    return [GATEWAY_DOCTOR_CAPABILITY];
  }

  supportsCapability(capabilityId: string): boolean {
    return String(capabilityId || "").trim() === GATEWAY_DOCTOR_CAPABILITY;
  }

  async handleCapabilityInvoke(
    frame: GatewayRequestEnvelope<GatewayToolInvokePayload>,
  ): Promise<Record<string, unknown>> {
    const capabilityId = String(frame.payload?.capability_id || "").trim();
    if (capabilityId !== GATEWAY_DOCTOR_CAPABILITY) {
      throw new Error(`Unsupported gateway doctor capability: ${capabilityId || "unknown"}`);
    }
    const args = frame.payload?.arguments ?? {};
    const repair = Boolean(args.repair);
    const run = await runGatewayDoctor(this.checks, this.ctx, { repair });
    return { capability_id: GATEWAY_DOCTOR_CAPABILITY, ...run };
  }
}

/**
 * Provisioning, end to end: locate + version-pin -> generate config ->
 * lock down -> apply -> verify by read-back -> audit -> supervise ->
 * report (CHANNEL-ADOPTION-PLAN.md step 4).
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE DRIFT DECISION, AND WHY IT IS THIS ONE
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * OpenClaw's config is a DERIVED ARTIFACT of Empyralis's stored policy — the
 * same category as `empyralis-runtime-kernel`'s compiled binary (CLAUDE.md,
 * MAN-306), and it fails the same way: built once from a source of truth,
 * never itself a source of truth, and silently enforcing yesterday's policy
 * the moment the two disagree. Nothing anywhere logs the disagreement,
 * because a message OpenClaw drops is a message we never see.
 *
 * Drift is not hypothetical here. Three real writers touch that file:
 *   - the box operator, with an editor;
 *   - OpenClaw itself (the in-chat `/activation mention|always` command
 *     rewrites `groups.<id>.requireMention`; several channels have a
 *     `configWrites` switch precisely because they write back);
 *   - a previous Empyralis provisioning run against an older policy.
 *
 * THE DECISION — regenerate-and-restart for content drift; refuse-to-run for
 * anything we cannot verify. Concretely:
 *
 *   drift in a path Empyralis generates
 *       -> REGENERATE + APPLY + RESTART, and journal
 *          `openclaw.provision.drift_corrected` with the paths that differed.
 *          Empyralis is the sole author of those paths; a local edit is not a
 *          second opinion, it is a stale build. Reconciling the other way
 *          (importing their config into our database) would make the owner's
 *          Empyralis settings a lagging mirror of a file they cannot see, and
 *          is never done.
 *
 *   post-apply read-back does not satisfy the lockdown or the policy
 *       -> REFUSE. The instance is not started, or is stopped if running,
 *          and the reason is reported. Never "start anyway and warn": a
 *          gateway that is bound wrong, unauthenticated, mDNS-advertised, or
 *          holding a model credential is the exact configuration that leaked
 *          tens of thousands of installs' chat history.
 *
 *   version is not the pin, or `openclaw security audit` is not clean
 *       -> REFUSE, before anything is applied at all.
 *
 *   a channel's policy is not expressible
 *       -> that CHANNEL fails closed (disabled) with a named reason; the rest
 *          of the instance still provisions. See openclaw-config-plan.ts.
 *
 * Why not "refuse to start on any drift"? Because the overwhelmingly common
 * cause is a legitimate policy change in Empyralis, and refusing to start
 * would take a customer's channels offline for doing exactly what the product
 * asks them to do. Why not "reconcile silently"? Because a silent correction
 * of a security-relevant policy is indistinguishable from not noticing. So:
 * correct it, and say so, every time.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * WHAT "VERIFY" MEANS HERE
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Every assertion is made against the EFFECTIVE config read back out of
 * OpenClaw (`openclaw config get <path> --json`), never against the document
 * we intended to write. `config patch` validates and merges; a merge can
 * leave a stale sibling in place, a schema change can drop a field, and an
 * include file can override. Checking our own intent would prove only that we
 * intended well.
 *
 * One honest limit, recorded rather than papered over: `config get` REDACTS
 * secrets (`gateway.auth.token` reads back as `__OPENCLAW_REDACTED__`). So
 * read-back proves a token is PRESENT, not that it is the one we wrote. The
 * token's correctness is proven a different way — the outbound WS client
 * authenticating with it (../openclaw-gateway-client.ts) — and a mismatch
 * there is loud (`openclaw.outbound.connect_rejected`, journaled per attempt).
 */

import { promises as fsp } from "fs";
import os from "os";
import path from "path";

import {
  auditOpenClawChannelShapes,
  resolveOpenClawPluginHookFlags,
  type OpenClawChannelShapeFinding,
} from "./openclaw-channel-shapes";
import { OpenClawCli, openClawProfileStateDir } from "./openclaw-cli";
import {
  detectForbiddenCredentialEnvNames,
  findOpenClawLockdownViolations,
  renderOpenClawConfig,
  type OpenClawLockdownViolation,
  type OpenClawPolicyFinding,
  type EmpyralisChannelPolicy,
  type OpenClawProvisioningPlan,
  type OpenClawSecrets,
} from "./openclaw-config-plan";
import { checkOpenClawVersion, type OpenClawVersionCheck } from "./openclaw-version";
import {
  auditAndRepairOpenClawSupervisorUnit,
  buildOpenClawSupervisedEnv,
  defaultOpenClawJobRegistrar,
  type OpenClawSupervisorInstallOutcome,
} from "./openclaw-supervisor-unit";

/** The config subtrees read back for verification.
 *
 * MUST cover every top-level key renderOpenClawConfig writes, or the missing
 * one reads back as absent and is reported as permanent, uncorrectable drift
 * on every single run — which would turn `openclaw_policy_not_applied` into
 * noise and then into something people ignore. Asserted by a test, because
 * that failure mode is silent (a passing provisioning run that always claims
 * drift is indistinguishable from one that never does).
 *
 * `openclaw config get` errors on a path that does not exist yet, which is
 * information rather than a failure: an absent subtree simply verifies as
 * absent, i.e. as drift on everything under it. */
const VERIFIED_CONFIG_SUBTREES = [
  "gateway",
  "discovery",
  "models",
  "security",
  "plugins",
  "channels",
  "agents",
  "tools",
] as const;

/**
 * Audit findings that mean "this check could not complete", not "this check
 * found a problem". They are reported like every other finding and never
 * silenced in OpenClaw's own config (the lockdown forbids
 * `security.audit.suppressions` entirely, precisely so "the audit is clean"
 * keeps meaning something), but they do not fail provisioning: refusing to
 * carry a customer's messages because their Docker daemon answered slowly is
 * not a security decision.
 *
 * Everything else at `warn` or `critical` fails the run.
 */
export const OPENCLAW_AUDIT_INCONCLUSIVE_CHECK_IDS: readonly string[] = [
  "sandbox.browser_container.docker_probe_timeout",
  "gateway.probe_failed",
  "gateway.probe_auth_secretref_unavailable",
];

/**
 * The ONE substantive finding this deployment acknowledges rather than fails
 * on — and it is safe to acknowledge only because the lockdown enforces the
 * precondition that makes it dangerous absent.
 *
 * `security.trust_model.multi_user_heuristic` fires whenever any channel has
 * an open or populated-allowlist inbound policy (their
 * `listPotentialMultiUserSignals`), i.e. on every configuration where anyone
 * can actually message the agent. It is not a misconfiguration report; it is
 * OpenClaw restating their own documented trust model — "one trusted operator
 * boundary per gateway, not hostile multi-tenant isolation" — the same
 * sentence CLAUDE.md already records as the reason we never adopt their agent
 * loop.
 *
 * Empyralis's answer to it is architectural, and all three parts are enforced
 * elsewhere in this file rather than asserted here:
 *   - ONE ISOLATED INSTANCE PER CUSTOMER (`--profile`, openclaw-cli.ts) — the
 *     multi-tenancy never lands inside one of their gateways;
 *   - NO TOOL AUTHORITY (OPENCLAW_DENIED_TOOLS + the tools.* lockdown
 *     expectations) — their own finding text degrades to "No unguarded
 *     runtime/process tools were detected by this heuristic" once that holds;
 *   - NO BRAIN (no model provider credential, in config or in the child env).
 *
 * If any of those regresses, the corresponding lockdown check fails and the
 * instance is refused before this list is ever consulted. So this
 * acknowledgement cannot outlive its own justification — which is the only
 * shape in which acknowledging a security finding is honest.
 *
 * It is deliberately NOT silenced via OpenClaw's own
 * `security.audit.suppressions` (which the lockdown forbids outright): a human
 * running `openclaw security audit` by hand on a customer's box must still see
 * it.
 */
export const OPENCLAW_AUDIT_ACKNOWLEDGED_CHECK_IDS: readonly string[] = [
  "security.trust_model.multi_user_heuristic",
];

export interface OpenClawAuditFinding {
  checkId: string;
  severity: string;
  title: string;
  detail?: string;
}

export type OpenClawProvisionStatus = "provisioned" | "refused";

export type OpenClawProvisionRefusalCode =
  | "openclaw_not_installed"
  | "openclaw_version_unreadable"
  | "openclaw_version_mismatch"
  | "openclaw_channel_shape_drift"
  | "openclaw_config_patch_failed"
  | "openclaw_lockdown_violated"
  | "openclaw_policy_not_applied"
  | "openclaw_security_audit_not_clean";

export interface OpenClawProvisionResult {
  status: OpenClawProvisionStatus;
  profile: string;
  version: OpenClawVersionCheck;
  /** Populated on refusal. The instance is NOT running when this is set. */
  refusal?: { code: OpenClawProvisionRefusalCode; detail: string };
  /** sha256 of the secret-free rendered config. */
  configFingerprint?: string;
  /** The fingerprint that was on disk before this run, if any. */
  previousConfigFingerprint?: string;
  /** True when this run changed what OpenClaw enforces. */
  configChanged: boolean;
  /** Config paths whose effective value did not match what we generated,
   *  BEFORE this run corrected them. Empty on a no-op run. */
  driftedPaths: string[];
  /** Channels deliberately left off, with the owner-facing reason. */
  disabledChannels: OpenClawPolicyFinding[];
  /** Deliberate loosenings of OpenClaw's policy relative to Empyralis's. */
  widenings: OpenClawPolicyFinding[];
  /** Transcription-vs-installed-schema differences (openclaw-channel-shapes). */
  shapeFindings: OpenClawChannelShapeFinding[];
  lockdownViolations: OpenClawLockdownViolation[];
  auditFindings: OpenClawAuditFinding[];
  /** Model-provider credential env vars found in this process's environment
   *  and therefore withheld from OpenClaw. Names only, never values. */
  strippedCredentialEnvNames: string[];
  supervisor?: OpenClawSupervisorInstallOutcome;
  /** True when the supervised instance answered a health probe afterwards. */
  healthy?: boolean;
}

export interface OpenClawProvisionerOptions {
  plan: OpenClawProvisioningPlan;
  secrets: OpenClawSecrets;
  /** The low-privilege loopback secret the bridge plugin uses to reach the
   *  Empyralis gateway's intake, and where that intake listens. */
  bridgeToken: string;
  bridgeEndpointUrl: string;
  cli: OpenClawCli;
  env?: NodeJS.ProcessEnv;
  platform?: NodeJS.Platform;
  homeDir?: string;
  /** Where the Empyralis gateway keeps its own state; the OpenClaw
   *  provisioning record and supervised-process logs live under it. */
  stateDir: string;
  /** Absolute path to the `openclaw` binary, for the supervisor unit. */
  binaryPath: string;
  record?: (messageType: string, payload: Record<string, unknown>) => Promise<unknown>;
  /** Injectable for tests. */
  fs?: {
    readFile: (filePath: string) => Promise<string>;
    writeFile: (filePath: string, contents: string) => Promise<void>;
    mkdir: (dirPath: string) => Promise<void>;
    rm: (filePath: string) => Promise<void>;
  };
  registerJob?: Parameters<typeof auditAndRepairOpenClawSupervisorUnit>[0]["registerJob"];
  /** Probes the provisioned instance. Injectable so a test never opens a
   *  socket. */
  probeHealth?: () => Promise<boolean>;
}

/**
 * The last-applied provisioning record, on the EMPYRALIS side.
 *
 * `channels` is stored deliberately: it is what makes a boot-time reconcile
 * possible at all. OpenClaw's config can change while nobody is watching —
 * the in-chat `/activation mention|always` command rewrites
 * `groups.<id>.requireMention` on the customer's own machine, with no cloud
 * round trip — so a gateway that only ever provisioned when the cloud pushed
 * would run indefinitely on a policy the owner never chose. With the last
 * policy on disk, every boot can re-render and re-assert it (see
 * OpenClawProvisioningRuntime.reconcileFromLastAppliedPolicy).
 *
 * It holds POLICY, never secrets: the gateway token comes from src/config.ts
 * on every run, so this file is safe to read while debugging a customer's box.
 */
interface StoredProvisioningRecord {
  fingerprint: string;
  appliedAt: string;
  pinnedVersion: string;
  channels: EmpyralisChannelPolicy[];
}

function defaultFs(): NonNullable<OpenClawProvisionerOptions["fs"]> {
  return {
    readFile: (filePath) => fsp.readFile(filePath, "utf8"),
    writeFile: async (filePath, contents) => {
      await fsp.writeFile(filePath, contents, { mode: 0o600 });
    },
    mkdir: async (dirPath) => {
      await fsp.mkdir(dirPath, { recursive: true });
    },
    rm: async (filePath) => {
      await fsp.rm(filePath, { force: true });
    },
  };
}

/** Where the last-applied fingerprint is recorded. Deliberately on the
 *  EMPYRALIS side, not inside the OpenClaw profile: a provenance marker that
 *  lives in the file it describes proves nothing (both are rewritten
 *  together), and OpenClaw's top-level config schema is
 *  `additionalProperties: false` so there is nowhere to put one anyway. */
export function openClawProvisioningRecordPath(stateDir: string, profile: string): string {
  return path.join(stateDir, "openclaw", `${profile}.provisioning.json`);
}

/** Flattens a config document to dotted leaf paths, so a drift report names
 *  the setting that changed rather than dumping two documents at a human. */
export function flattenConfigPaths(value: unknown, prefix = ""): Map<string, unknown> {
  const out = new Map<string, unknown>();
  if (value && typeof value === "object" && !Array.isArray(value)) {
    for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
      const nextPrefix = prefix ? `${prefix}.${key}` : key;
      const nested = flattenConfigPaths(child, nextPrefix);
      if (nested.size === 0) out.set(nextPrefix, child);
      else for (const [k, v] of nested) out.set(k, v);
    }
    return out;
  }
  if (prefix) out.set(prefix, value);
  return out;
}

/**
 * Which generated paths the live instance does not currently agree with.
 *
 * ONE-DIRECTIONAL on purpose: every path Empyralis generates must match, but
 * extra paths present in the effective config are NOT drift. OpenClaw ships
 * dozens of defaults and writes its own `meta.lastTouchedAt` on every save;
 * treating those as drift would report a correction on every single run and
 * train everyone to ignore the signal.
 *
 * Secret values are excluded — `config get` redacts them, so comparing would
 * report permanent, uncorrectable drift on `gateway.auth.token` forever.
 */
export function findConfigDrift(
  generated: Record<string, unknown>,
  effective: Record<string, unknown>,
  ignoredPaths: readonly string[] = ["gateway.auth.token"],
): string[] {
  const wanted = flattenConfigPaths(generated);
  const actual = flattenConfigPaths(effective);
  const drifted: string[] = [];
  for (const [dottedPath, expected] of wanted) {
    if (ignoredPaths.includes(dottedPath)) continue;
    const found = actual.get(dottedPath);
    if (JSON.stringify(found) !== JSON.stringify(expected)) drifted.push(dottedPath);
  }
  return drifted.sort();
}

/** critical/warn findings that are not merely inconclusive probes. */
export function blockingAuditFindings(findings: OpenClawAuditFinding[]): OpenClawAuditFinding[] {
  return findings.filter((finding) => {
    const severity = String(finding.severity || "").toLowerCase();
    if (severity !== "critical" && severity !== "warn") return false;
    if (OPENCLAW_AUDIT_INCONCLUSIVE_CHECK_IDS.includes(finding.checkId)) return false;
    return !OPENCLAW_AUDIT_ACKNOWLEDGED_CHECK_IDS.includes(finding.checkId);
  });
}

function parseAuditFindings(report: unknown): OpenClawAuditFinding[] {
  const findings = report && typeof report === "object" ? (report as Record<string, unknown>).findings : undefined;
  if (!Array.isArray(findings)) return [];
  return findings
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    .map((item) => ({
      checkId: String(item.checkId ?? ""),
      severity: String(item.severity ?? ""),
      title: String(item.title ?? ""),
      detail: typeof item.detail === "string" ? item.detail : undefined,
    }));
}

export class OpenClawProvisioner {
  private readonly fs: NonNullable<OpenClawProvisionerOptions["fs"]>;
  private readonly env: NodeJS.ProcessEnv;
  private readonly platform: NodeJS.Platform;
  private readonly homeDir: string;

  constructor(private readonly options: OpenClawProvisionerOptions) {
    this.fs = options.fs ?? defaultFs();
    this.env = options.env ?? process.env;
    this.platform = options.platform ?? process.platform;
    this.homeDir = options.homeDir ?? os.homedir();
  }

  private async record(messageType: string, payload: Record<string, unknown>): Promise<void> {
    try {
      await this.options.record?.(messageType, payload);
    } catch {
      // Auditing must never take provisioning down.
    }
  }

  private async readStoredRecord(): Promise<StoredProvisioningRecord | undefined> {
    try {
      const raw = await this.fs.readFile(
        openClawProvisioningRecordPath(this.options.stateDir, this.options.cli.profile),
      );
      const parsed = JSON.parse(raw) as StoredProvisioningRecord;
      return typeof parsed?.fingerprint === "string" ? parsed : undefined;
    } catch {
      return undefined;
    }
  }

  private async writeStoredRecord(fingerprint: string, pinnedVersion: string): Promise<void> {
    const filePath = openClawProvisioningRecordPath(this.options.stateDir, this.options.cli.profile);
    await this.fs.mkdir(path.dirname(filePath));
    const record: StoredProvisioningRecord = {
      fingerprint,
      appliedAt: new Date().toISOString(),
      pinnedVersion,
      channels: this.options.plan.channels,
    };
    await this.fs.writeFile(filePath, JSON.stringify(record, null, 2));
  }

  /** The policy this box last successfully applied, for a boot-time
   *  reconcile. Undefined when nothing has ever been provisioned here. */
  async lastAppliedChannelPolicies(): Promise<EmpyralisChannelPolicy[] | undefined> {
    const stored = await this.readStoredRecord();
    return Array.isArray(stored?.channels) ? stored?.channels : undefined;
  }

  /** Reads back the subtrees this module asserts on, assembling a partial
   *  effective document. A subtree OpenClaw reports as absent is simply
   *  omitted, which then shows up as drift on every path under it — correct,
   *  since "absent" is exactly what an unapplied config looks like. */
  private async readEffectiveConfig(): Promise<Record<string, unknown>> {
    const effective: Record<string, unknown> = {};
    for (const subtree of VERIFIED_CONFIG_SUBTREES) {
      const result = await this.options.cli.run(["config", "get", subtree, "--json"], { timeoutMs: 30_000 });
      if (result.code !== 0) continue;
      try {
        effective[subtree] = JSON.parse(result.stdout);
      } catch {
        // Unparseable output is treated as absent; the drift comparison then
        // reports every path under it rather than silently passing.
      }
    }
    return effective;
  }

  private refuse(
    base: OpenClawProvisionResult,
    code: OpenClawProvisionRefusalCode,
    detail: string,
  ): OpenClawProvisionResult {
    void this.record("openclaw.provision.refused", { profile: base.profile, code, detail });
    return { ...base, status: "refused", refusal: { code, detail } };
  }

  async provision(): Promise<OpenClawProvisionResult> {
    const profile = this.options.cli.profile;
    const strippedCredentialEnvNames = detectForbiddenCredentialEnvNames(this.env);

    // ── 1. Version pin, before anything is touched ──────────────────────
    const version = checkOpenClawVersion(await this.options.cli.version());
    const base: OpenClawProvisionResult = {
      status: "refused",
      profile,
      version,
      configChanged: false,
      driftedPaths: [],
      disabledChannels: [],
      widenings: [],
      shapeFindings: [],
      lockdownViolations: [],
      auditFindings: [],
      strippedCredentialEnvNames,
    };
    if (!version.ok) {
      return this.refuse(base, version.code ?? "openclaw_version_mismatch", version.detail ?? "");
    }

    // ── 2. The installed schema still has the shape the mapping assumes ──
    const schema = await this.options.cli.configSchema();
    const channelIds = this.options.plan.channels.map((channel) => channel.channelId);
    const shapeFindings = auditOpenClawChannelShapes(schema, channelIds);
    const hooks = resolveOpenClawPluginHookFlags(schema, channelIds);
    shapeFindings.push(...hooks.findings);
    base.shapeFindings = shapeFindings;
    if (shapeFindings.length > 0) {
      return this.refuse(
        base,
        "openclaw_channel_shape_drift",
        "The installed OpenClaw's config schema no longer matches the policy mapping Empyralis was built against: " +
          shapeFindings.map((finding) => `${finding.code} (${finding.channelId}): ${finding.detail}`).join(" | ") +
          " Refusing to provision rather than write a config whose policy meaning we can no longer vouch for.",
      );
    }

    // ── 3. Generate ─────────────────────────────────────────────────────
    const rendered = renderOpenClawConfig(
      {
        ...this.options.plan,
        // Always this instance's OWN state dir, never whatever a caller
        // happened to put in the plan — the workspace pin is an isolation
        // control, and an isolation control does not take a hint.
        profileStateDir: openClawProfileStateDir(profile, this.homeDir),
        pluginHookFlags: hooks.enable,
      },
      this.options.secrets,
    );
    base.configFingerprint = rendered.fingerprint;
    base.disabledChannels = rendered.disabledChannels;
    base.widenings = rendered.widenings;

    const stored = await this.readStoredRecord();
    base.previousConfigFingerprint = stored?.fingerprint;

    // ── 4. Detect drift against what the instance ACTUALLY enforces ─────
    const effectiveBefore = await this.readEffectiveConfig();
    const driftedPaths = findConfigDrift(rendered.config, effectiveBefore);
    base.driftedPaths = driftedPaths;

    if (driftedPaths.length > 0) {
      await this.record("openclaw.provision.drift_corrected", {
        profile,
        drifted_paths: driftedPaths,
        previous_fingerprint: stored?.fingerprint ?? null,
        fingerprint: rendered.fingerprint,
        // Named explicitly so the journal answers "who moved my policy":
        // this is the regenerate-and-restart branch, not a refusal.
        resolution: "regenerated_from_empyralis_policy",
      });
    }

    // ── 5. Apply ────────────────────────────────────────────────────────
    const patchPath = path.join(
      openClawProfileStateDir(profile, this.homeDir),
      "empyralis-generated.config.json",
    );
    await this.fs.mkdir(path.dirname(patchPath));
    await this.fs.writeFile(patchPath, JSON.stringify(rendered.config, null, 2));
    const patch = await this.options.cli.configPatch(patchPath);
    // The rendered document carries the gateway token; it must not outlive
    // the single `config patch` call that consumes it.
    await this.fs.rm(patchPath);
    if (patch.code !== 0) {
      return this.refuse(
        base,
        "openclaw_config_patch_failed",
        `OpenClaw rejected the generated config: ${patch.stderr.trim() || patch.stdout.trim() || `exit ${patch.code}`}`,
      );
    }
    base.configChanged = driftedPaths.length > 0 || stored?.fingerprint !== rendered.fingerprint;

    // ── 6. Verify by READ-BACK, not by intent ───────────────────────────
    const effectiveAfter = await this.readEffectiveConfig();
    const lockdownViolations = findOpenClawLockdownViolations(effectiveAfter);
    base.lockdownViolations = lockdownViolations;
    if (lockdownViolations.length > 0) {
      return this.refuse(
        base,
        "openclaw_lockdown_violated",
        "The provisioned OpenClaw instance does not satisfy the mandatory security lockdown after applying the " +
          "generated config: " +
          lockdownViolations.map((violation) => `${violation.code} at ${violation.path}: ${violation.detail}`).join(" | "),
      );
    }
    const residualDrift = findConfigDrift(rendered.config, effectiveAfter);
    if (residualDrift.length > 0) {
      return this.refuse(
        base,
        "openclaw_policy_not_applied",
        "These settings did not take effect after the config was applied, so the owner's Empyralis policy would " +
          `not describe what this channel actually does: ${residualDrift.join(", ")}.`,
      );
    }

    // ── 7. Their own audit, treated as a gate ───────────────────────────
    const { report } = await this.options.cli.securityAuditJson();
    const auditFindings = parseAuditFindings(report);
    base.auditFindings = auditFindings;
    const blocking = blockingAuditFindings(auditFindings);
    if (blocking.length > 0) {
      return this.refuse(
        base,
        "openclaw_security_audit_not_clean",
        "`openclaw security audit` is not clean: " +
          blocking.map((finding) => `${finding.checkId} [${finding.severity}] ${finding.title}`).join(" | "),
      );
    }

    // ── 8. Record, supervise, probe ─────────────────────────────────────
    await this.writeStoredRecord(rendered.fingerprint, version.expected);

    const supervisor = await auditAndRepairOpenClawSupervisorUnit(
      {
        profile,
        binaryPath: this.options.binaryPath,
        gatewayPort: this.options.plan.gatewayPort,
        environment: this.supervisedEnvironment(),
        platform: this.platform,
        homeDir: this.homeDir,
        logDir: path.join(this.options.stateDir, "logs"),
        readFile: this.fs.readFile,
        writeFile: this.fs.writeFile,
        mkdir: this.fs.mkdir,
        registerJob: this.options.registerJob ?? defaultOpenClawJobRegistrar(this.platform),
      },
      true,
    );

    const healthy = this.options.probeHealth ? await this.options.probeHealth() : undefined;

    const result: OpenClawProvisionResult = {
      ...base,
      status: "provisioned",
      supervisor,
      healthy,
    };
    await this.record("openclaw.provision.applied", {
      profile,
      fingerprint: rendered.fingerprint,
      previous_fingerprint: stored?.fingerprint ?? null,
      config_changed: result.configChanged,
      drifted_paths: driftedPaths,
      disabled_channels: rendered.disabledChannels.map((finding) => ({
        channel_id: finding.channelId,
        code: finding.code,
      })),
      widenings: rendered.widenings.map((finding) => ({ channel_id: finding.channelId, code: finding.code })),
      audit_findings: auditFindings.map((finding) => ({ check_id: finding.checkId, severity: finding.severity })),
      stripped_credential_env: strippedCredentialEnvNames,
      supervisor_action: supervisor.repair?.action ?? null,
      healthy: healthy ?? null,
    });
    return result;
  }

  private supervisedEnvironment(): Record<string, string> {
    return buildOpenClawSupervisedEnv({
      profile: this.options.cli.profile,
      homeDir: this.homeDir,
      pathEnv: String(this.env.PATH || "/usr/local/bin:/usr/bin:/bin"),
      bridgeToken: this.options.bridgeToken,
      bridgeEndpointUrl: this.options.bridgeEndpointUrl,
    });
  }
}

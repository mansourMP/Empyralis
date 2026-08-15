/**
 * `openclaw.provision` — the capability the cloud invokes to make this box's
 * OpenClaw instance match the workspace's stored policy.
 *
 * Same wiring shape as GatewaySelfUpdateRuntime / GatewayDoctorRuntime
 * (requestedCapabilities / supportsCapability / handleCapabilityInvoke,
 * constructed in index.ts, dispatched by GatewayCapabilityRouter) — no new
 * transport, no second control channel. The cloud side is
 * server_modules/openclaw_provisioning_service.py.
 *
 * WHAT THE CLOUD SENDS, AND WHAT IT DELIBERATELY DOES NOT
 * -------------------------------------------------------
 * It sends POLICY, in Empyralis's own vocabulary (dm_policy / group_policy
 * exactly as personal_channels_service.py stores them). It does not send an
 * OpenClaw config, and it never will: rendering lives on the box, in one
 * place, next to the version pin and the schema audit that decide whether a
 * given rendering is even valid against the installed build. A cloud that
 * rendered OpenClaw config would be a second renderer that cannot see which
 * OpenClaw is installed.
 *
 * It also does not send secrets. The gateway token for OpenClaw and the
 * loopback bridge secret are already on this box (src/config.ts's
 * openclawGatewayToken / openclawBridgeToken) — the same values the outbound
 * WS client and the inbound listener already use. Provisioning writes the one
 * OpenClaw needs into the config it generates, so a box is configured once and
 * the two halves can never disagree about which token is current.
 */

import os from "os";
import path from "path";

import type { GatewayConfig } from "../../config";
import { openClawGatewayPortFromUrl } from "../../config";
import type { GatewayRequestEnvelope, GatewayToolInvokePayload } from "../../protocol/types";
import { OPENCLAW_TRANSPORT_CHANNEL_IDS } from "../capabilities";
import { OPENCLAW_INBOUND_PATH } from "../inbound-listener";
import { OpenClawCli, openClawProfileStateDir } from "./openclaw-cli";
import {
  OpenClawProvisioner,
  type OpenClawProvisionResult,
} from "./openclaw-provisioner";
import type {
  EmpyralisChannelPolicy,
  EmpyralisDmPolicyMode,
  EmpyralisGroupPolicyMode,
} from "./openclaw-config-plan";
import { OPENCLAW_PINNED_VERSION } from "./openclaw-version";

export const OPENCLAW_PROVISION_CAPABILITY = "openclaw.provision";

const DM_POLICY_MODES: readonly EmpyralisDmPolicyMode[] = ["owner_only", "allowlist", "pairing", "open"];
const GROUP_POLICY_MODES: readonly EmpyralisGroupPolicyMode[] = ["open", "allowlist", "disabled"];

export interface OpenClawProvisioningRuntimeOptions {
  /** `--profile <name>`. One per customer instance. */
  profile: string;
  /** Where OpenClaw's own gateway listens (its port), derived from the
   *  loopback URL the outbound client already uses so the two can never
   *  point at different ports. */
  gatewayPort: number;
  /** OpenClaw's own gateway auth token — the same secret
   *  ../openclaw-gateway-client.ts authenticates with. */
  gatewayToken: string;
  /** The low-privilege loopback secret shared with the bridge plugin. */
  bridgeToken: string;
  bridgeEndpointUrl: string;
  /** Absolute path to the bridge plugin directory, loaded into OpenClaw. */
  bridgePluginPath: string;
  stateDir: string;
  binaryPath?: string;
  env?: NodeJS.ProcessEnv;
  platform?: NodeJS.Platform;
  homeDir?: string;
  record?: (messageType: string, payload: Record<string, unknown>) => Promise<unknown>;
  /** Test seam: replaces the whole provisioning run. */
  runProvision?: (
    channels: EmpyralisChannelPolicy[],
    registryPlugins?: readonly { npmPackage: string; installSpec: string; pluginId: string }[],
  ) => Promise<OpenClawProvisionResult>;
}

/**
 * Validates the cloud's policy payload into the render input.
 *
 * Strict, and fail-closed on every axis: an unrecognized mode is NOT coerced
 * to a default (that is how a "disabled" becomes an "open"), and an
 * unrecognized channel id is refused rather than passed through to a config
 * write. `require_mention` defaults TRUE when absent — matching
 * personal_channels_service.DEFAULT_REQUIRE_MENTION, and being the safe side
 * of the one axis Empyralis cannot re-enforce itself.
 */
/**
 * Validates the cloud's REGISTRY plugin list — the channel plugins that come
 * from OpenClaw's plugin registry rather than its bundled catalog.
 *
 * Fail-closed like `parseChannelPolicies`: an entry missing any of the three
 * fields is an error, never a partially-formed install. There is no channel
 * id to validate against here on purpose — a registry plugin's channel id is
 * not knowable until it is installed, so the cloud's own derived manifest is
 * the allowlist and this only checks the shape it sent.
 */
export function parseRegistryPluginSpecs(raw: unknown): {
  registryPlugins: { npmPackage: string; installSpec: string; pluginId: string }[];
  errors: string[];
} {
  const errors: string[] = [];
  const registryPlugins: { npmPackage: string; installSpec: string; pluginId: string }[] = [];
  if (raw === undefined || raw === null) return { registryPlugins, errors };
  if (!Array.isArray(raw)) {
    return { registryPlugins, errors: ["`registry_plugins` must be an array."] };
  }
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") {
      errors.push("A registry plugin entry was not an object.");
      continue;
    }
    const record = entry as Record<string, unknown>;
    const npmPackage = String(record.npm_package ?? "").trim();
    const installSpec = String(record.install_spec ?? "").trim();
    const pluginId = String(record.plugin_id ?? "").trim().toLowerCase();
    if (!npmPackage || !installSpec || !pluginId) {
      errors.push(
        `Registry plugin entry is missing npm_package/install_spec/plugin_id: ${JSON.stringify(entry)}`,
      );
      continue;
    }
    registryPlugins.push({ npmPackage, installSpec, pluginId });
  }
  return { registryPlugins, errors };
}

export function parseChannelPolicies(raw: unknown): {
  channels: EmpyralisChannelPolicy[];
  errors: string[];
} {
  const errors: string[] = [];
  const channels: EmpyralisChannelPolicy[] = [];
  if (!Array.isArray(raw)) {
    return { channels, errors: ["`channels` must be an array of channel policies."] };
  }
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") {
      errors.push("A channel policy entry was not an object.");
      continue;
    }
    const record = entry as Record<string, unknown>;
    const channelId = String(record.channel_id ?? "").trim().toLowerCase();
    if (!OPENCLAW_TRANSPORT_CHANNEL_IDS.includes(channelId)) {
      errors.push(
        `"${channelId}" is not a channel this gateway transports through OpenClaw ` +
          `(${OPENCLAW_TRANSPORT_CHANNEL_IDS.join(", ")}).`,
      );
      continue;
    }
    const dm = (record.dm_policy ?? {}) as Record<string, unknown>;
    const group = (record.group_policy ?? {}) as Record<string, unknown>;
    const dmMode = String(dm.mode ?? "").trim().toLowerCase() as EmpyralisDmPolicyMode;
    const groupMode = String(group.mode ?? "").trim().toLowerCase() as EmpyralisGroupPolicyMode;
    if (!DM_POLICY_MODES.includes(dmMode)) {
      errors.push(`${channelId}: dm_policy.mode "${String(dm.mode)}" is not one of ${DM_POLICY_MODES.join(", ")}.`);
      continue;
    }
    if (!GROUP_POLICY_MODES.includes(groupMode)) {
      errors.push(
        `${channelId}: group_policy.mode "${String(group.mode)}" is not one of ${GROUP_POLICY_MODES.join(", ")}.`,
      );
      continue;
    }
    const toList = (value: unknown): string[] =>
      Array.isArray(value)
        ? [...new Set(value.map((item) => String(item).trim()).filter((item) => item.length > 0))].sort()
        : [];
    channels.push({
      channelId,
      enabled: record.enabled !== false,
      // Opt-IN, and fail-closed the other way round from `enabled`: an absent
      // or unreadable flag means "do not fetch third-party code onto this
      // customer's machine". Installing a plugin nobody asked for is the
      // expensive, irreversible direction; not installing one is a reported
      // state (`channel_plugins[].installed: false`) the cloud can act on.
      installPlugin: record.install_plugin === true,
      dmPolicy: { mode: dmMode, allowlist: toList(dm.allowlist) },
      groupPolicy: {
        mode: groupMode,
        allowlist: toList(group.allowlist),
        requireMention: group.require_mention === undefined ? true : Boolean(group.require_mention),
      },
    });
  }
  return { channels, errors };
}

export class OpenClawProvisioningRuntime {
  constructor(private readonly options: OpenClawProvisioningRuntimeOptions) {}

  requestedCapabilities(): string[] {
    return [OPENCLAW_PROVISION_CAPABILITY];
  }

  supportsCapability(capabilityId: string): boolean {
    return String(capabilityId || "").trim() === OPENCLAW_PROVISION_CAPABILITY;
  }

  async handleCapabilityInvoke(
    frame: GatewayRequestEnvelope<GatewayToolInvokePayload>,
  ): Promise<Record<string, unknown>> {
    const capabilityId = String(frame.payload?.capability_id || "").trim();
    if (capabilityId !== OPENCLAW_PROVISION_CAPABILITY) {
      throw new Error(`Unsupported OpenClaw provisioning capability: ${capabilityId || "unknown"}`);
    }
    const args = frame.payload?.arguments ?? {};
    const parsed = parseChannelPolicies((args as Record<string, unknown>).channels);
    const parsedRegistry = parseRegistryPluginSpecs(
      (args as Record<string, unknown>).registry_plugins,
    );
    if (parsed.errors.length > 0 || parsedRegistry.errors.length > 0) {
      // Never provision from a partially-understood policy: a channel we
      // silently skipped would keep whatever policy the last run left, which
      // is the stale-artifact failure this whole step exists to prevent.
      return {
        capability_id: OPENCLAW_PROVISION_CAPABILITY,
        status: "refused",
        refusal: {
          code: "openclaw_policy_payload_invalid",
          detail: [...parsed.errors, ...parsedRegistry.errors].join(" "),
        },
      };
    }
    const result = await this.runProvision(parsed.channels, parsedRegistry.registryPlugins);
    return { capability_id: OPENCLAW_PROVISION_CAPABILITY, pinned_version: OPENCLAW_PINNED_VERSION, ...serialize(result) };
  }

  private buildProvisioner(
    channels: EmpyralisChannelPolicy[],
    registryPlugins?: readonly { npmPackage: string; installSpec: string; pluginId: string }[],
  ): OpenClawProvisioner {
    const env = this.options.env ?? process.env;
    const platform = this.options.platform ?? process.platform;
    const homeDir = this.options.homeDir ?? os.homedir();
    const cli = new OpenClawCli({
      profile: this.options.profile,
      binaryPath: this.options.binaryPath,
      env,
    });
    return new OpenClawProvisioner({
      plan: {
        profile: this.options.profile,
        gatewayPort: this.options.gatewayPort,
        // Overwritten by the provisioner with the real per-profile path; see
        // its call to renderOpenClawConfig.
        profileStateDir: openClawProfileStateDir(this.options.profile, homeDir),
        bridgePluginPath: this.options.bridgePluginPath,
        channels,
        registryPlugins,
      },
      secrets: { gatewayToken: this.options.gatewayToken },
      bridgeToken: this.options.bridgeToken,
      bridgeEndpointUrl: this.options.bridgeEndpointUrl,
      cli,
      env,
      platform,
      homeDir,
      stateDir: this.options.stateDir,
      // Passed through UNRESOLVED and possibly undefined, on purpose. This
      // used to default to the bare string "openclaw", which is correct for
      // OpenClawCli's execFile (it does a PATH lookup) and fatal for a
      // supervisor unit (launchd resolves ProgramArguments[0] against its own
      // minimal PATH, systemd rejects a non-absolute ExecStart outright) —
      // and this is the path EVERY gateway takes at boot, so every unit it
      // ever wrote was a job that could not start. The provisioner resolves
      // an absolute path itself, after the install step. See
      // ./openclaw-binary-path.ts.
      binaryPath: this.options.binaryPath,
      record: this.options.record,
    });
  }

  async runProvision(
    channels: EmpyralisChannelPolicy[],
    registryPlugins?: readonly { npmPackage: string; installSpec: string; pluginId: string }[],
  ): Promise<OpenClawProvisionResult> {
    if (this.options.runProvision) return this.options.runProvision(channels, registryPlugins);
    return this.buildProvisioner(channels, registryPlugins).provision();
  }

  /**
   * Boot-time reconcile, called from index.ts once the cloud scope is active.
   *
   * Re-renders and re-asserts the LAST POLICY THE CLOUD PUSHED, read from this
   * box's own provisioning record. This is the half that catches drift nobody
   * is watching: OpenClaw's in-chat `/activation mention|always` command
   * rewrites `groups.<id>.requireMention` on the customer's machine with no
   * cloud round trip, and the box operator has an editor. Without this, a
   * gateway would run indefinitely on a policy the owner never chose, and the
   * Empyralis settings screen would describe a policy that is not in force —
   * the exact silent-misrouting failure step 4 exists to prevent.
   *
   * Returns undefined when this box has never been provisioned (nothing to
   * reconcile TO). Callers wanting a box to come up channel-ready with no
   * cloud round trip use ensureProvisionedAtBoot() below instead.
   */
  async reconcileFromLastAppliedPolicy(): Promise<OpenClawProvisionResult | undefined> {
    const provisioner = this.buildProvisioner([]);
    const channels = await provisioner.lastAppliedChannelPolicies();
    if (!channels) return undefined;
    return this.runProvision(channels);
  }

  /**
   * What index.ts calls on boot: reconcile a box that has a policy, and
   * BASELINE a box that has never been provisioned at all.
   *
   * ═══════════════════════════════════════════════════════════════════════
   * WHY A FRESH BOX MUST PROVISION ITSELF, WITH NO CLOUD TRIGGER
   * ═══════════════════════════════════════════════════════════════════════
   *
   * The requirement is that a customer presses one button, authorises
   * DigitalOcean, and comes back to a machine where everything is installed —
   * never learning that OpenClaw exists. Before this, a freshly provisioned
   * box did nothing about OpenClaw until somebody called
   * `POST /personal-channels/openclaw/gateways/{id}/provision`, and the boot
   * reconcile was a documented no-op on exactly the boxes that needed it most:
   *
   *   BEFORE                               AFTER
   *   boot ─▶ stored policy? ─ no ─▶ ✗     boot ─▶ stored policy?
   *                          └ yes ─▶ rec.         ├ yes ─▶ reconcile
   *                                                └ no  ─▶ BASELINE []
   *   nothing installed, nothing           openclaw@pin installed, locked
   *   configured, no unit, until a         down, audited, supervised —
   *   human triggers the route             before any channel exists
   *
   * THE BASELINE IS THE EMPTY POLICY, AND THAT IS NOT A SHORTCUT. Every step
   * that makes an instance safe is channel-independent: the version pin, the
   * loopback bind, token auth, mDNS off, no model credential, no tool
   * authority, the plugin allowlist, `openclaw security audit`, the pinned
   * agent workspace and the supervised unit. What zero channels means is
   * exactly zero inbound policy — nothing enabled, nothing reachable, and no
   * third-party channel plugin fetched (install scope is opt-in per channel
   * and an empty plan asks for none). The box is left running, locked down,
   * and carrying no traffic until the owner enables something.
   *
   * Baselining with a GUESSED channel set would be the opposite: minutes of
   * network and twenty third-party packages running beside a customer's
   * messages, for channels they may never use — and an inbound policy nobody
   * chose, which is the failure the whole gating design exists to prevent.
   *
   * IDEMPOTENT BY CONSTRUCTION: the second boot finds a stored record and
   * takes the reconcile branch, which is itself a no-op when nothing drifted.
   */
  async ensureProvisionedAtBoot(): Promise<{
    result: OpenClawProvisionResult;
    mode: "reconciled" | "baseline";
  }> {
    const channels = await this.buildProvisioner([]).lastAppliedChannelPolicies();
    if (channels) {
      return { result: await this.runProvision(channels), mode: "reconciled" };
    }
    return { result: await this.runProvision([]), mode: "baseline" };
  }
}

/** snake_case for the cloud, matching every other capability result on this
 *  transport. */
function serialize(result: OpenClawProvisionResult): Record<string, unknown> {
  return {
    status: result.status,
    profile: result.profile,
    refusal: result.refusal ?? null,
    openclaw_version: result.version.observed ?? null,
    config_fingerprint: result.configFingerprint ?? null,
    previous_config_fingerprint: result.previousConfigFingerprint ?? null,
    config_changed: result.configChanged,
    drifted_paths: result.driftedPaths,
    disabled_channels: result.disabledChannels.map((finding) => ({
      channel_id: finding.channelId,
      code: finding.code,
      detail: finding.detail,
    })),
    widenings: result.widenings.map((finding) => ({
      channel_id: finding.channelId,
      code: finding.code,
      detail: finding.detail,
    })),
    lockdown_violations: result.lockdownViolations,
    // The plugin half of "is this channel actually usable". `installed: false`
    // means no plugin; `installed: true` plus a failing send means no
    // credential. Reported structurally so nothing downstream has to classify
    // OpenClaw's send-error prose to tell the two apart.
    channel_plugins: result.channelPlugins.map((state) => ({
      channel_id: state.channelId,
      plugin_id: state.pluginId ?? null,
      requires_plugin: state.requiresPlugin,
      installed: state.installed,
      resolved_spec: state.resolvedSpec ?? null,
      action: state.action,
    })),
    // The registry half of the same question, keyed by npm package because a
    // registry plugin has no channel id until it is installed.
    // `revealed_channel_ids` is the ONLY place that id can be learned, and it
    // is observed off the box, never derived from the package name.
    registry_plugins: result.registryPlugins.map((state) => ({
      npm_package: state.npmPackage,
      install_spec: state.installSpec,
      plugin_id: state.pluginId,
      installed: state.installed,
      resolved_spec: state.resolvedSpec ?? null,
      action: state.action,
      revealed_channel_ids: state.revealedChannelIds,
    })),
    audit_findings: result.auditFindings.map((finding) => ({
      check_id: finding.checkId,
      severity: finding.severity,
      title: finding.title,
    })),
    stripped_credential_env: result.strippedCredentialEnvNames,
    // How the `openclaw` CLI itself got here. `already_installed` on every
    // steady-state run; `installed` exactly once per box; `failed` carries
    // the refusal above. Surfaced so the product can say "this computer could
    // not reach the software registry" instead of the customer-facing
    // nonsense of "OpenClaw is not installed".
    runtime_install: result.runtimeInstall
      ? {
          action: result.runtimeInstall.action,
          observed_version: result.runtimeInstall.observedVersion ?? null,
          expected_version: result.runtimeInstall.expectedVersion,
        }
      : null,
    supervisor: result.supervisor
      ? {
          supported: result.supervisor.supported,
          // "this OS has no supervisor we manage" and "openclaw is not on this
          // box" are different facts, and only the second is actionable.
          unsupported_reason: result.supervisor.unsupportedReason ?? null,
          file_state: result.supervisor.fileState,
          action: result.supervisor.repair?.action ?? null,
          requires_manual_reload: result.supervisor.repair?.requiresManualReload ?? null,
          detail: result.supervisor.repair?.detail ?? null,
        }
      : null,
    healthy: result.healthy ?? null,
    restart_required: result.restartRequired,
  };
}

/** Default bridge plugin location inside a packaged gateway install. */
export function defaultBridgePluginPath(entryPath: string): string {
  // dist/index.js -> <package root>/openclaw-bridge-plugin
  return path.resolve(path.dirname(entryPath), "..", "openclaw-bridge-plugin");
}

/**
 * The ONE place an `OpenClawProvisioningRuntime` is constructed from a
 * gateway config.
 *
 * Two callers need an identical one: index.ts, on every boot, and
 * ./openclaw-install-plan-cli.ts, once at install time under the root
 * installer (which has to provision BEFORE it starts the systemd unit, or the
 * unit's first start finds no config and restart-loops until something else
 * happens to write one). Two hand-rolled constructions of the same eleven
 * arguments is how a box ends up provisioned against a different port, a
 * different bridge endpoint or a different profile than the one it runs — all
 * silent, all presenting as a channel that simply never answers.
 */
export function buildOpenClawProvisioningRuntime(params: {
  config: GatewayConfig;
  /** Both loopback secrets, already resolved — ../openclaw-local-secrets.ts. */
  secrets: { bridgeToken: string; gatewayToken: string };
  /** The dist/index.js this process was launched from, for the bridge plugin
   *  path. The install-time caller passes its own entry, which resolves to the
   *  same package root. */
  entryPath: string;
  env?: NodeJS.ProcessEnv;
  record?: (messageType: string, payload: Record<string, unknown>) => Promise<unknown>;
}): OpenClawProvisioningRuntime {
  const { config, secrets } = params;
  return new OpenClawProvisioningRuntime({
    profile: config.openclawProfile,
    // Derived from the SAME url the outbound WS client dials, so the port we
    // provision OpenClaw to listen on and the port we connect to can never be
    // two different numbers.
    gatewayPort: openClawGatewayPortFromUrl(config.openclawGatewayUrl),
    gatewayToken: secrets.gatewayToken,
    bridgeToken: secrets.bridgeToken,
    bridgeEndpointUrl: `http://127.0.0.1:${config.openclawBridgePort}${OPENCLAW_INBOUND_PATH}`,
    bridgePluginPath: config.openclawBridgePluginPath || defaultBridgePluginPath(params.entryPath),
    stateDir: config.stateDir,
    binaryPath: config.openclawBinaryPath,
    env: params.env,
    record: params.record,
  });
}

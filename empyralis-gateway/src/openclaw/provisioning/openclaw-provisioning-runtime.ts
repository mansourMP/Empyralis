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

import type { GatewayRequestEnvelope, GatewayToolInvokePayload } from "../../protocol/types";
import { OPENCLAW_TRANSPORT_CHANNEL_IDS } from "../capabilities";
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
  runProvision?: (channels: EmpyralisChannelPolicy[]) => Promise<OpenClawProvisionResult>;
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
    if (parsed.errors.length > 0) {
      // Never provision from a partially-understood policy: a channel we
      // silently skipped would keep whatever policy the last run left, which
      // is the stale-artifact failure this whole step exists to prevent.
      return {
        capability_id: OPENCLAW_PROVISION_CAPABILITY,
        status: "refused",
        refusal: { code: "openclaw_policy_payload_invalid", detail: parsed.errors.join(" ") },
      };
    }
    const result = await this.runProvision(parsed.channels);
    return { capability_id: OPENCLAW_PROVISION_CAPABILITY, pinned_version: OPENCLAW_PINNED_VERSION, ...serialize(result) };
  }

  private buildProvisioner(channels: EmpyralisChannelPolicy[]): OpenClawProvisioner {
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
      },
      secrets: { gatewayToken: this.options.gatewayToken },
      bridgeToken: this.options.bridgeToken,
      bridgeEndpointUrl: this.options.bridgeEndpointUrl,
      cli,
      env,
      platform,
      homeDir,
      stateDir: this.options.stateDir,
      binaryPath: this.options.binaryPath || "openclaw",
      record: this.options.record,
    });
  }

  async runProvision(channels: EmpyralisChannelPolicy[]): Promise<OpenClawProvisionResult> {
    if (this.options.runProvision) return this.options.runProvision(channels);
    return this.buildProvisioner(channels).provision();
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
   * reconcile TO — inventing a policy here would be worse than waiting for the
   * cloud to push one).
   */
  async reconcileFromLastAppliedPolicy(): Promise<OpenClawProvisionResult | undefined> {
    const provisioner = this.buildProvisioner([]);
    const channels = await provisioner.lastAppliedChannelPolicies();
    if (!channels) return undefined;
    return this.runProvision(channels);
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
    audit_findings: result.auditFindings.map((finding) => ({
      check_id: finding.checkId,
      severity: finding.severity,
      title: finding.title,
    })),
    stripped_credential_env: result.strippedCredentialEnvNames,
    supervisor: result.supervisor
      ? {
          supported: result.supervisor.supported,
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

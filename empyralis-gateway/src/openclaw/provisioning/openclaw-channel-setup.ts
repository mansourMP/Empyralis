/**
 * `openclaw.channel_setup` — reading and writing a channel's CREDENTIAL on the
 * box, from the browser, with no terminal and no SSH.
 *
 * Sibling of `openclaw.provision`, deliberately NOT part of it. Provisioning
 * writes the POLICY Empyralis owns and regenerates it from our database on
 * every run; a credential is the one value the OWNER supplies and Empyralis
 * must never regenerate. Fusing them would mean either a reprovision that
 * wipes a credential, or a credential write that drags a whole policy render
 * behind it. They are separate because they answer to different authorities.
 *
 * THE THREE STATES, NEVER COLLAPSED
 * ---------------------------------
 * OpenClaw's own `channels list` prints three independent facts per channel,
 * and a person needs all three to fix their own problem:
 *
 *     - Feishu: not installed, configured, disabled, run openclaw plugins …
 *                └ plugin      └ credential  └ switch
 *
 * A single "Connected / Not connected" light destroys exactly the information
 * that says WHICH of the three to go fix. So this capability reports them
 * separately, from separate reads:
 *
 *     installed    `channels list --all --json` -> chat.<id>.installed
 *                  (their registry's own answer, not an inference)
 *     configured   every credential field the manifest derives for the
 *                  channel, each reported `set: true|false` — strictly better
 *                  than one boolean, because "configured" without saying WHICH
 *                  field is missing is the same unhelpful collapse one level
 *                  down
 *     enabled      `channels.<id>.enabled` in the effective config
 *
 * THE SECRET NEVER COMES BACK OUT, AND THAT IS STRUCTURAL
 * ------------------------------------------------------
 * The read is `openclaw config get channels --json`, which REDACTS every
 * secret-typed value at the source: OpenClaw returns the literal
 * `__OPENCLAW_REDACTED__` in place of `appSecret`, `botToken` and friends. So
 * this process never holds a stored credential in the first place — there is
 * no "remember not to return it" rule to forget, and no redaction of our own
 * that could regress. `set` is computed from presence, never from the value,
 * and a value is never placed in a result, a log line, or an argv.
 *
 * WHAT MAY BE WRITTEN IS A CLOSED SET
 * -----------------------------------
 * A write may only touch `channels.<id>.<field>` where `<id>` is a channel
 * this gateway transports and `<field>` is in the manifest's derived
 * credential shape for that channel. That matters because this capability
 * accepts a path fragment from the cloud: without it, a compromised or
 * confused caller could patch `gateway.auth.token`, `tools.profile` or
 * `plugins.allow` and walk straight through the lockdown provisioning exists
 * to enforce. The allowlist is not hand-written — it is the same generated
 * artifact the setup form is rendered from, so it cannot drift from the UI.
 */

import path from "path";
import { promises as fsp } from "fs";

import { GENERATED_OPENCLAW_MANIFEST } from "../generated-openclaw-channels";
import type { GeneratedOpenClawCredentialField } from "../generated-openclaw-channels";
import { OPENCLAW_TRANSPORT_CHANNEL_IDS } from "../capabilities";
import { OpenClawCli, openClawProfileStateDir } from "./openclaw-cli";

export const OPENCLAW_CHANNEL_SETUP_CAPABILITY = "openclaw.channel_setup";

/** OpenClaw's own placeholder for a redacted secret in `config get --json`.
 *  Treated as "a value is present", never as a value. */
const OPENCLAW_REDACTED = "__OPENCLAW_REDACTED__";

const READ_TIMEOUT_MS = 30_000;

export type OpenClawChannelSetupAction = "read" | "write_credential";

export interface OpenClawCredentialFieldState {
  readonly name: string;
  readonly secret: boolean;
  readonly type: string;
  /** Whether OpenClaw's effective config carries a value for this field.
   *  Presence only — the value is never read, and for a secret it is never
   *  even transmitted to this process. */
  readonly set: boolean;
}

export interface OpenClawChannelSetupState {
  readonly channel_id: string;
  readonly channel_key: string;
  readonly label: string;
  readonly connect_method: string;
  readonly selection_label: string;
  readonly docs_path: string | null;
  /** Plugin present on this box. `false` means the channel physically cannot
   *  run yet, whatever the credential says. */
  readonly installed: boolean;
  readonly requires_plugin: boolean;
  readonly plugin_id: string | null;
  /** `channels.<id>.enabled` in the effective config. */
  readonly enabled: boolean;
  /** True when every derived credential field carries a value. Reported
   *  BESIDE the per-field list, never instead of it. */
  readonly configured: boolean;
  readonly fields: readonly OpenClawCredentialFieldState[];
  /** OpenClaw's own account ids for this channel, when its plugin is loaded
   *  and could resolve one. Empty is not an error — it is what a channel with
   *  no plugin or no credential looks like. */
  readonly accounts: readonly string[];
}

export interface OpenClawChannelSetupResult {
  readonly capability_id: string;
  readonly status: "ok" | "refused";
  readonly refusal: { code: string; detail: string } | null;
  readonly channels: readonly OpenClawChannelSetupState[];
  /** Set only by a write, and only about the fields the write touched.
   *  Carries names, never values. */
  readonly written_fields: readonly string[];
  readonly restart_required: boolean;
}

interface ChannelListEntry {
  installed?: unknown;
  accounts?: unknown;
  origin?: unknown;
}

export interface OpenClawChannelSetupRuntimeOptions {
  profile: string;
  binaryPath?: string;
  env?: NodeJS.ProcessEnv;
  homeDir?: string;
  cli?: OpenClawCli;
  record?: (messageType: string, payload: Record<string, unknown>) => Promise<unknown>;
}

/** The manifest's derived credential fields for a channel, or `undefined` when
 *  the channel is not one this gateway transports. */
export function credentialFieldsForChannel(
  channelId: string,
): readonly GeneratedOpenClawCredentialField[] | undefined {
  const normalized = String(channelId || "").trim().toLowerCase();
  if (!OPENCLAW_TRANSPORT_CHANNEL_IDS.includes(normalized)) return undefined;
  const channel = GENERATED_OPENCLAW_MANIFEST.channels.find((entry) => entry.id === normalized);
  return channel?.credential_shape.fields;
}

/**
 * Validates a write request into the exact set of config assignments it is
 * allowed to make.
 *
 * Fail-closed on every axis, and each refusal names the axis rather than a
 * generic "invalid": an unknown channel, an unknown field, a field on a
 * channel that pairs instead of pasting, a non-string value, or a value that
 * is OpenClaw's own redaction placeholder (which would mean the browser echoed
 * a masked read back at us and we would be writing the literal string
 * `__OPENCLAW_REDACTED__` into a customer's credential).
 */
export function parseCredentialWrite(raw: unknown): {
  channelId: string;
  values: Record<string, string>;
  errors: string[];
} {
  const errors: string[] = [];
  const record = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const channelId = String(record.channel_id ?? "").trim().toLowerCase();
  const fields = credentialFieldsForChannel(channelId);
  if (!fields) {
    return {
      channelId,
      values: {},
      errors: [`"${channelId}" is not a channel this gateway transports through OpenClaw.`],
    };
  }
  if (fields.length === 0) {
    return {
      channelId,
      values: {},
      errors: [
        `${channelId} does not take a pasted credential — OpenClaw declares no credential field ` +
          `for it, so it links some other way (QR, local pairing, or an inbound webhook).`,
      ],
    };
  }
  const allowed = new Map(fields.map((field) => [field.name, field]));
  const rawValues = (record.values && typeof record.values === "object" ? record.values : {}) as Record<
    string,
    unknown
  >;
  const values: Record<string, string> = {};
  for (const [name, value] of Object.entries(rawValues)) {
    const field = allowed.get(name);
    if (!field) {
      // Named, not silently dropped: a dropped field is a save that reports
      // success and changes nothing, which is indistinguishable from working.
      errors.push(
        `${channelId}: "${name}" is not a credential field OpenClaw declares for this channel ` +
          `(${fields.map((entry) => entry.name).join(", ")}).`,
      );
      continue;
    }
    if (typeof value !== "string") {
      errors.push(`${channelId}.${name}: value must be a string.`);
      continue;
    }
    if (value === OPENCLAW_REDACTED) {
      errors.push(
        `${channelId}.${name}: received OpenClaw's redaction placeholder rather than a value. ` +
          `A masked field must be left out of the request, not echoed back.`,
      );
      continue;
    }
    const trimmed = value.trim();
    if (!trimmed) {
      // An empty string is not a decision. "Leave this one alone" is expressed
      // by omitting the key; clearing a credential is a separate, deliberate
      // action and is not offered here.
      errors.push(`${channelId}.${name}: empty value. Omit the field to leave it unchanged.`);
      continue;
    }
    values[name] = trimmed;
  }
  if (Object.keys(values).length === 0 && errors.length === 0) {
    errors.push(`${channelId}: no credential fields were supplied.`);
  }
  return { channelId, values, errors };
}

export class OpenClawChannelSetupRuntime {
  private readonly cli: OpenClawCli;

  constructor(private readonly options: OpenClawChannelSetupRuntimeOptions) {
    this.cli =
      options.cli ??
      new OpenClawCli({
        profile: options.profile,
        binaryPath: options.binaryPath,
        env: options.env ?? process.env,
      });
  }

  requestedCapabilities(): string[] {
    return [OPENCLAW_CHANNEL_SETUP_CAPABILITY];
  }

  supportsCapability(capabilityId: string): boolean {
    return String(capabilityId || "").trim() === OPENCLAW_CHANNEL_SETUP_CAPABILITY;
  }

  async handleCapabilityInvoke(frame: {
    payload?: { capability_id?: unknown; arguments?: unknown };
  }): Promise<Record<string, unknown>> {
    const capabilityId = String(frame.payload?.capability_id || "").trim();
    if (capabilityId !== OPENCLAW_CHANNEL_SETUP_CAPABILITY) {
      throw new Error(`Unsupported OpenClaw channel-setup capability: ${capabilityId || "unknown"}`);
    }
    const args = (frame.payload?.arguments ?? {}) as Record<string, unknown>;
    const action = String(args.action ?? "read").trim();
    if (action === "read") return { ...(await this.read()) };
    if (action === "write_credential") return { ...(await this.writeCredential(args)) };
    return {
      ...this.refuse(
        "openclaw_channel_setup_action_unknown",
        `"${action}" is not an action of ${OPENCLAW_CHANNEL_SETUP_CAPABILITY} (read, write_credential).`,
      ),
    };
  }

  /** The three states, each from its own read. */
  async read(): Promise<OpenClawChannelSetupResult> {
    const [listed, effective] = await Promise.all([this.readChannelList(), this.readChannelConfig()]);
    return {
      capability_id: OPENCLAW_CHANNEL_SETUP_CAPABILITY,
      status: "ok",
      refusal: null,
      channels: this.projectChannels(listed, effective),
      written_fields: [],
      restart_required: false,
    };
  }

  async writeCredential(args: Record<string, unknown>): Promise<OpenClawChannelSetupResult> {
    const parsed = parseCredentialWrite(args);
    if (parsed.errors.length > 0) {
      return this.refuse("openclaw_channel_credential_invalid", parsed.errors.join(" "));
    }

    // The patch file carries the plaintext credential, so it is written under
    // the profile's own state dir at 0600, consumed by exactly one
    // `config patch`, and removed whether or not that call succeeded. The same
    // shape openclaw-provisioner.ts uses for the gateway token, for the same
    // reason: a secret must not reach argv (where it is world-readable in the
    // process table) and must not outlive the call that consumes it.
    const stateDir = openClawProfileStateDir(this.options.profile, this.options.homeDir);
    const patchPath = path.join(stateDir, `empyralis-credential-${parsed.channelId}.json`);
    let patchCode = -1;
    let patchDetail = "";
    try {
      await fsp.mkdir(path.dirname(patchPath), { recursive: true });
      await fsp.writeFile(
        patchPath,
        JSON.stringify({ channels: { [parsed.channelId]: parsed.values } }, null, 2),
        { mode: 0o600 },
      );
      const patch = await this.cli.configPatch(patchPath);
      patchCode = patch.code;
      patchDetail = patch.stderr.trim() || patch.stdout.trim() || `exit ${patch.code}`;
    } finally {
      await fsp.rm(patchPath, { force: true }).catch(() => undefined);
    }

    if (patchCode !== 0) {
      // OpenClaw's own rejection prose is surfaced, but it is never parsed:
      // this branch is chosen by the exit code, which is the stable fact.
      return this.refuse("openclaw_channel_credential_patch_failed", patchDetail);
    }

    await this.options.record?.("openclaw.channel_setup.credential_written", {
      profile: this.options.profile,
      channel_id: parsed.channelId,
      // Names only. A journal that carried the value would be the leak this
      // whole capability is shaped to prevent, and a journal is durable.
      fields: Object.keys(parsed.values).sort(),
    });

    // Read back rather than report intent — the same posture provisioning
    // takes. If OpenClaw accepted the patch but resolved the value to nothing,
    // the caller sees `set: false` and knows the save did not take.
    const [listed, effective] = await Promise.all([this.readChannelList(), this.readChannelConfig()]);
    return {
      capability_id: OPENCLAW_CHANNEL_SETUP_CAPABILITY,
      status: "ok",
      refusal: null,
      channels: this.projectChannels(listed, effective),
      written_fields: Object.keys(parsed.values).sort(),
      // A credential change is picked up when the supervised unit restarts;
      // this capability, like provisioning, never restarts OpenClaw itself
      // (`gateway.restart.request` is scoped operator.admin, which stays out of
      // reach permanently — it is the only scope under which OpenClaw honours
      // a client-asserted senderIsOwner).
      restart_required: true,
    };
  }

  private refuse(code: string, detail: string): OpenClawChannelSetupResult {
    return {
      capability_id: OPENCLAW_CHANNEL_SETUP_CAPABILITY,
      status: "refused",
      refusal: { code, detail },
      channels: [],
      written_fields: [],
      restart_required: false,
    };
  }

  private async readChannelList(): Promise<Record<string, ChannelListEntry>> {
    const result = await this.cli.run(["channels", "list", "--all", "--json"], {
      timeoutMs: READ_TIMEOUT_MS,
    });
    if (result.code !== 0) return {};
    try {
      const parsed = JSON.parse(result.stdout) as { chat?: unknown };
      return parsed.chat && typeof parsed.chat === "object"
        ? (parsed.chat as Record<string, ChannelListEntry>)
        : {};
    } catch {
      return {};
    }
  }

  /** `config get channels --json` — secrets already redacted BY OPENCLAW. */
  private async readChannelConfig(): Promise<Record<string, Record<string, unknown>>> {
    const result = await this.cli.run(["config", "get", "channels", "--json"], {
      timeoutMs: READ_TIMEOUT_MS,
    });
    if (result.code !== 0) return {};
    try {
      const parsed = JSON.parse(result.stdout);
      return parsed && typeof parsed === "object"
        ? (parsed as Record<string, Record<string, unknown>>)
        : {};
    } catch {
      return {};
    }
  }

  private projectChannels(
    listed: Record<string, ChannelListEntry>,
    effective: Record<string, Record<string, unknown>>,
  ): OpenClawChannelSetupState[] {
    const states: OpenClawChannelSetupState[] = [];
    for (const channel of GENERATED_OPENCLAW_MANIFEST.channels) {
      if (!OPENCLAW_TRANSPORT_CHANNEL_IDS.includes(channel.id)) continue;
      const entry = listed[channel.id] ?? {};
      const config = effective[channel.id] ?? {};
      const shape = channel.credential_shape;
      const fields: OpenClawCredentialFieldState[] = shape.fields.map((field) => ({
        name: field.name,
        secret: field.secret,
        type: field.type,
        // Presence, never value. For a secret the only thing that ever arrives
        // here is OpenClaw's redaction placeholder.
        set: hasValue(config[field.name]),
      }));
      states.push({
        channel_id: channel.id,
        channel_key: channel.channel_key,
        label: channel.label,
        connect_method: shape.connect_method,
        selection_label: shape.selection_label,
        docs_path: shape.docs_path,
        installed: entry.installed === true,
        requires_plugin: channel.plugin_install?.required === true,
        plugin_id: channel.plugin_install?.plugin_id ?? null,
        enabled: config.enabled === true,
        configured: fields.length > 0 && fields.every((field) => field.set),
        fields,
        accounts: Array.isArray(entry.accounts)
          ? entry.accounts.map((account) => String(account)).filter((account) => account.length > 0)
          : [],
      });
    }
    return states;
  }
}

function hasValue(value: unknown): boolean {
  if (value === undefined || value === null) return false;
  if (typeof value === "string") return value.trim().length > 0;
  if (Array.isArray(value)) return value.length > 0;
  return true;
}

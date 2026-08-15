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
import {
  deriveOpenClawCredentialShapes,
  type DerivedOpenClawCredentialShape,
} from "./openclaw-channel-credential-shape";
import {
  readChannelLinkShapes,
  projectLinkResponse,
  type ChannelLoginHttpRequest,
  type OpenClawChannelLinkOutcome,
  type OpenClawChannelLinkShape,
} from "./openclaw-channel-link";

export const OPENCLAW_CHANNEL_SETUP_CAPABILITY = "openclaw.channel_setup";

/** The bridge plugin's own loopback route. Declared here as a constant rather
 *  than built from a caller-supplied string: this capability already accepts a
 *  channel id from the cloud, and a caller-chosen URL would be a far larger
 *  door than a caller-chosen field name. */
const CHANNEL_LOGIN_ROUTE_PATH = "/api/v1/empyralis/channel-login";

/** Ceiling on one in-band link call. `wait` blocks until the scan lands or the
 *  code rotates — measured at ~20s per rotation against real WhatsApp — so this
 *  has to outlast a rotation while staying inside the cloud's own capability
 *  timeout. */
const LINK_TIMEOUT_MS = 55_000;

/** OpenClaw's own placeholder for a redacted secret in `config get --json`.
 *  Treated as "a value is present", never as a value. */
const OPENCLAW_REDACTED = "__OPENCLAW_REDACTED__";

const READ_TIMEOUT_MS = 30_000;

export type OpenClawChannelSetupAction =
  | "read"
  | "write_credential"
  | "link_start"
  | "link_wait";

export interface OpenClawCredentialFieldState {
  readonly name: string;
  readonly secret: boolean;
  readonly type: string;
  /** Whether OpenClaw's effective config carries a value for this field.
   *  Presence only — the value is never read, and for a secret it is never
   *  even transmitted to this process. */
  readonly set: boolean;
  /** False for the fields a customer must supply to connect. Carried here as
   *  well as in the catalog because a LIVE-derived channel has no catalog
   *  entry to read it from — see `connect_method` below. */
  readonly advanced: boolean;
  readonly file_alternative: string | null;
}

export interface OpenClawChannelSetupState {
  readonly channel_id: string;
  readonly channel_key: string;
  readonly label: string;
  /**
   * How this channel connects, AS THIS BOX SEES IT.
   *
   * Normally the manifest's own answer. For a channel the manifest calls
   * `plugin_absent` — its plugin contributed no `channels.<id>` node on the
   * machine the manifest was generated from — this is re-derived from THIS
   * box's `openclaw config schema` once the plugin is actually installed here,
   * and reports `credential`/`pairing` with the real fields.
   *
   * The manifest is the pre-install BELIEF; this is the OBSERVATION, and the
   * observation wins. It stays `plugin_absent` when the installed plugin still
   * contributes no node — an honest unknown, never a guess.
   */
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
  /** How this channel is LINKED, when it takes no pasted credential — derived
   *  live from the box's own plugin metadata, never from a list here. `null`
   *  means OpenClaw did not report on this channel at all (normally: it is not
   *  enabled yet), which is a different fact from "it has no link flow" and is
   *  reported as its own value rather than as `supports_qr_login: false`. */
  readonly link: OpenClawChannelLinkShape | null;
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
  /** Set only by `link_start`/`link_wait`. Absent on every other action rather
   *  than present-and-empty, so a caller cannot mistake "this action does not
   *  link" for "the link produced nothing". */
  readonly link?: OpenClawChannelLinkOutcome & { channel_id: string };
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
  /** OpenClaw's own gateway HTTP origin, e.g. `http://127.0.0.1:18789`.
   *  Derived from the WebSocket URL the outbound client already uses, so there
   *  is one configured address for OpenClaw on this box, not two. */
  openclawHttpUrl?: string;
  /** The gateway shared secret. The SAME token the outbound WS client already
   *  presents — minted locally by openclaw-local-secrets.ts and written into
   *  OpenClaw's own config by provisioning, so both ends are ours and there is
   *  nothing here for a person to supply. */
  openclawGatewayToken?: string;
  /** Seam for tests. Production passes nothing and `fetch` is used. */
  linkFetch?: (url: string, init: {
    method: string;
    headers: Record<string, string>;
    body: string;
  }) => Promise<{ ok: boolean; status: number; json(): Promise<unknown> }>;
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

/** True when the MANIFEST admits it cannot describe this channel's form — its
 *  plugin contributed no `channels.<id>` node to the machine the manifest was
 *  generated from. The only case in which a live schema read is allowed to
 *  answer instead. */
export function manifestCannotDescribeChannel(channelId: string): boolean {
  const normalized = String(channelId || "").trim().toLowerCase();
  const channel = GENERATED_OPENCLAW_MANIFEST.channels.find((entry) => entry.id === normalized);
  return channel?.credential_shape.connect_method === "plugin_absent";
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
export function parseCredentialWrite(
  raw: unknown,
  /**
   * The fields THIS BOX's schema declares, for a channel the manifest calls
   * `plugin_absent`. The allowlist is still closed and still comes from
   * OpenClaw — from the installed schema instead of the pinned one, because
   * for these four channels the pinned one has nothing to say. Ignored when
   * the manifest already describes the channel: a live read must never be able
   * to WIDEN a channel the pinned build already answered for.
   */
  liveFields?: readonly GeneratedOpenClawCredentialField[],
): {
  channelId: string;
  values: Record<string, string>;
  errors: string[];
} {
  const errors: string[] = [];
  const record = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const channelId = String(record.channel_id ?? "").trim().toLowerCase();
  const manifestFields = credentialFieldsForChannel(channelId);
  // `plugin_absent`, never merely "no fields": a PAIRING channel's empty list
  // is the manifest's positive answer that there is nothing to paste, and a
  // live read must not be able to turn that into a form.
  const fields =
    manifestFields && manifestCannotDescribeChannel(channelId) && liveFields && liveFields.length > 0
      ? liveFields
      : manifestFields;
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
    if (action === "link_start" || action === "link_wait") {
      return { ...(await this.link(action, args)) };
    }
    return {
      ...this.refuse(
        "openclaw_channel_setup_action_unknown",
        `"${action}" is not an action of ${OPENCLAW_CHANNEL_SETUP_CAPABILITY} ` +
          `(read, write_credential, link_start, link_wait).`,
      ),
    };
  }

  /** Start, or continue waiting on, a channel link.
   *
   *  `link_start` asks for a code; `link_wait` blocks until that code is
   *  scanned or ROTATES. The caller passes the code it is currently showing
   *  and gets back either `linked: true` or a DIFFERENT code — which is what
   *  makes an expired square impossible rather than merely unlikely. Measured
   *  live against real WhatsApp: `wait` returned a new code after 20.2s with
   *  OpenClaw's own message "QR refreshed."
   *
   *  A code is a credential in flight. It is never journaled, never logged and
   *  never written to disk anywhere on this path. */
  async link(
    action: "link_start" | "link_wait",
    args: Record<string, unknown>,
  ): Promise<OpenClawChannelSetupResult> {
    const channelId = String(args.channel_id ?? "").trim().toLowerCase();
    if (!OPENCLAW_TRANSPORT_CHANNEL_IDS.includes(channelId)) {
      return this.refuse(
        "openclaw_channel_link_channel_unknown",
        `"${channelId || "(missing)"}" is not a channel this gateway transports.`,
      );
    }
    if (!this.options.openclawGatewayToken) {
      return this.refuse(
        "openclaw_channel_link_unconfigured",
        "This computer has no OpenClaw gateway secret yet, so a channel cannot be linked from here.",
      );
    }

    // A channel plugin only registers its login provider once its channel is
    // ENABLED, so an unenabled channel answers `web login provider is not
    // available` — a sentence that reads like a broken feature and is in fact
    // a missing switch. Flip it first, and say so in the journal. This is also
    // the honest moment to enable: the owner is, right now, setting the
    // channel up (CLAUDE.md: only enable channels the owner actually set up).
    if (action === "link_start") {
      const enabled = await this.ensureChannelEnabled(channelId);
      if (enabled.status === "refused") return enabled;
    }

    const request: ChannelLoginHttpRequest = {
      action: action === "link_start" ? "start" : "wait",
      timeoutMs: LINK_TIMEOUT_MS,
      ...(args.force === true ? { force: true } : {}),
      ...(typeof args.account_id === "string" && args.account_id.trim()
        ? { accountId: args.account_id.trim() }
        : {}),
      ...(action === "link_wait" && typeof args.current_qr_data_url === "string"
        ? { currentQrDataUrl: args.current_qr_data_url }
        : {}),
    };

    let outcome: OpenClawChannelLinkOutcome;
    try {
      outcome = projectLinkResponse(await this.callChannelLogin(request));
    } catch (error) {
      return this.refuse(
        "openclaw_channel_link_unreachable",
        error instanceof Error ? error.message : String(error),
      );
    }

    await this.options.record?.("openclaw.channel_setup.link_attempted", {
      profile: this.options.profile,
      channel_id: channelId,
      action,
      // Whether a code exists, never the code. A journal is durable, and a
      // pairing code in a durable record is a credential someone else can use.
      produced_code: outcome.qr_data_url !== null,
      linked: outcome.linked,
      status: outcome.status,
    });

    const [listed, effective] = await Promise.all([
      this.readChannelList(),
      this.readChannelConfig(),
    ]);
    const [links, live] = await Promise.all([
      this.readLinkShapes(),
      this.readLiveCredentialShapes(listed),
    ]);
    return {
      capability_id: OPENCLAW_CHANNEL_SETUP_CAPABILITY,
      status: "ok",
      refusal: null,
      channels: this.projectChannels(listed, effective, links, live),
      written_fields: [],
      // A completed link writes its own auth state inside OpenClaw and the
      // channel is started by the login handler itself, so nothing here needs
      // a restart to take effect.
      restart_required: false,
      link: { ...outcome, channel_id: channelId },
    };
  }

  private async callChannelLogin(request: ChannelLoginHttpRequest): Promise<unknown> {
    const origin = (this.options.openclawHttpUrl || "http://127.0.0.1:18789").replace(/\/+$/, "");
    const doFetch = this.options.linkFetch ?? ((url, init) => fetch(url, init));
    const response = await doFetch(`${origin}${CHANNEL_LOGIN_ROUTE_PATH}`, {
      method: "POST",
      headers: {
        // The same shared secret the outbound WS client already presents.
        authorization: `Bearer ${this.options.openclawGatewayToken}`,
        "content-type": "application/json",
      },
      body: JSON.stringify(request),
    });
    return await response.json();
  }

  /** `channels.<id>.enabled = true`, if it is not already.
   *
   *  Reads first so the common case writes nothing: a config patch per poll
   *  would rewrite the owner's config on a timer for no reason. */
  private async ensureChannelEnabled(channelId: string): Promise<OpenClawChannelSetupResult> {
    const effective = await this.readChannelConfig();
    if ((effective[channelId] ?? {}).enabled === true) {
      return { ...this.emptyOk() };
    }
    const stateDir = openClawProfileStateDir(this.options.profile, this.options.homeDir);
    const patchPath = path.join(stateDir, `empyralis-enable-${channelId}.json`);
    let code = -1;
    let detail = "";
    try {
      await fsp.mkdir(path.dirname(patchPath), { recursive: true });
      await fsp.writeFile(
        patchPath,
        JSON.stringify({ channels: { [channelId]: { enabled: true } } }, null, 2),
        { mode: 0o600 },
      );
      const patch = await this.cli.configPatch(patchPath);
      code = patch.code;
      detail = patch.stderr.trim() || patch.stdout.trim() || `exit ${patch.code}`;
    } finally {
      await fsp.rm(patchPath, { force: true }).catch(() => undefined);
    }
    if (code !== 0) {
      return this.refuse("openclaw_channel_enable_failed", detail);
    }
    await this.options.record?.("openclaw.channel_setup.channel_enabled", {
      profile: this.options.profile,
      channel_id: channelId,
    });
    return { ...this.emptyOk() };
  }

  private emptyOk(): OpenClawChannelSetupResult {
    return {
      capability_id: OPENCLAW_CHANNEL_SETUP_CAPABILITY,
      status: "ok",
      refusal: null,
      channels: [],
      written_fields: [],
      restart_required: false,
    };
  }

  private async readLinkShapes(): Promise<Record<string, OpenClawChannelLinkShape>> {
    try {
      return await readChannelLinkShapes(this.cli);
    } catch {
      // A capabilities read that fails is "we do not know how this channel is
      // linked", which every consumer already handles as `link: null`. It is
      // never a reason to fail a read that otherwise succeeded.
      return {};
    }
  }

  /** The three states, each from its own read — plus HOW an uncredentialed
   *  channel is linked, which is a fourth independent read and a fourth
   *  independent fact. */
  async read(): Promise<OpenClawChannelSetupResult> {
    const [listed, effective, links] = await Promise.all([
      this.readChannelList(),
      this.readChannelConfig(),
      this.readLinkShapes(),
    ]);
    const live = await this.readLiveCredentialShapes(listed);
    return {
      capability_id: OPENCLAW_CHANNEL_SETUP_CAPABILITY,
      status: "ok",
      refusal: null,
      channels: this.projectChannels(listed, effective, links, live),
      written_fields: [],
      restart_required: false,
    };
  }

  async writeCredential(args: Record<string, unknown>): Promise<OpenClawChannelSetupResult> {
    // For a channel the manifest cannot describe, the allowlist the write is
    // narrowed to comes from this box's own schema — otherwise a form the read
    // path now renders could never be submitted, which is a dead control with
    // extra steps. Nothing is read for any other channel.
    const requestedChannelId = String(
      (args && typeof args === "object" ? (args as Record<string, unknown>).channel_id : "") ?? "",
    )
      .trim()
      .toLowerCase();
    const liveFields = manifestCannotDescribeChannel(requestedChannelId)
      ? (await this.deriveFromLiveSchema([requestedChannelId]))[requestedChannelId]?.fields
      : undefined;
    const parsed = parseCredentialWrite(args, liveFields);
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
    const [listed, effective, links] = await Promise.all([
      this.readChannelList(),
      this.readChannelConfig(),
      this.readLinkShapes(),
    ]);
    const live = await this.readLiveCredentialShapes(listed);
    return {
      capability_id: OPENCLAW_CHANNEL_SETUP_CAPABILITY,
      status: "ok",
      refusal: null,
      channels: this.projectChannels(listed, effective, links, live),
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

  /**
   * Which channels this box can answer for that the MANIFEST could not, and
   * their real fields.
   *
   * The gate is deliberately narrow and is checked BEFORE any shell-out:
   * `config schema` is ~2.5MB and slow, so it is read AT MOST ONCE per call
   * and only when at least one channel actually needs it. On every box where
   * none of the four plugin-absent channels is installed — which is every box
   * today — this makes no CLI call at all.
   */
  private async readLiveCredentialShapes(
    listed: Record<string, ChannelListEntry>,
  ): Promise<Record<string, DerivedOpenClawCredentialShape>> {
    const needed = GENERATED_OPENCLAW_MANIFEST.channels
      .filter(
        (channel) =>
          OPENCLAW_TRANSPORT_CHANNEL_IDS.includes(channel.id) &&
          channel.credential_shape.connect_method === "plugin_absent" &&
          listed[channel.id]?.installed === true,
      )
      .map((channel) => channel.id);
    if (needed.length === 0) return {};
    return this.deriveFromLiveSchema(needed);
  }

  private async deriveFromLiveSchema(
    channelIds: readonly string[],
  ): Promise<Record<string, DerivedOpenClawCredentialShape>> {
    let schema: unknown;
    try {
      schema = await this.cli.configSchema();
    } catch {
      // A schema read that fails is "we still do not know this channel's
      // fields", which is exactly the state the manifest already describes.
      // Never a reason to fail a read that otherwise succeeded.
      return {};
    }
    if (!schema) return {};
    return deriveOpenClawCredentialShapes(schema, channelIds);
  }

  private projectChannels(
    listed: Record<string, ChannelListEntry>,
    effective: Record<string, Record<string, unknown>>,
    links: Record<string, OpenClawChannelLinkShape> = {},
    live: Record<string, DerivedOpenClawCredentialShape> = {},
  ): OpenClawChannelSetupState[] {
    const states: OpenClawChannelSetupState[] = [];
    for (const channel of GENERATED_OPENCLAW_MANIFEST.channels) {
      if (!OPENCLAW_TRANSPORT_CHANNEL_IDS.includes(channel.id)) continue;
      const entry = listed[channel.id] ?? {};
      const config = effective[channel.id] ?? {};
      const shape = channel.credential_shape;
      // The observation wins over the belief, and only ever for the channels
      // the belief admits it cannot describe — a channel the pinned build
      // declares a node for is answered by the manifest exactly as before.
      const observedShape = live[channel.id];
      const connectMethod = observedShape?.connect_method ?? shape.connect_method;
      const shapeFields: readonly {
        name: string;
        secret: boolean;
        type: string;
        advanced: boolean;
        file_alternative: string | null;
      }[] = observedShape?.fields ?? shape.fields;
      const fields: OpenClawCredentialFieldState[] = shapeFields.map((field) => ({
        name: field.name,
        secret: field.secret,
        type: field.type,
        advanced: field.advanced,
        file_alternative: field.file_alternative,
        // Presence, never value. For a secret the only thing that ever arrives
        // here is OpenClaw's redaction placeholder.
        set: hasValue(config[field.name]),
      }));
      states.push({
        channel_id: channel.id,
        channel_key: channel.channel_key,
        label: channel.label,
        connect_method: connectMethod,
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
        link: links[channel.id] ?? null,
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

/**
 * What each OpenClaw channel's config can and cannot express about policy —
 * transcribed from openclaw@2026.6.10's own `openclaw config schema` output
 * (2.5MB JSON Schema, read 2026-08-08), and re-verified against that schema
 * at provisioning time by auditOpenClawChannelShapes() below rather than
 * trusted as a comment.
 *
 * WHY THIS TABLE EXISTS AT ALL — the channels are NOT uniform, and the
 * differences are exactly where a policy silently stops being enforced:
 *
 *   channel  dmPolicy modes                groupPolicy modes         requireMention
 *   feishu   open pairing allowlist        open allowlist disabled   channel + groups{}
 *   line     open allowlist pairing disabled  open allowlist disabled          groups{}
 *   qqbot    open allowlist disabled       open allowlist disabled            groups{}
 *   zalo     pairing allowlist open disabled  open disabled allowlist   NEITHER  <-- the hole
 *   msteams  pairing allowlist open disabled  open disabled allowlist   channel + teams{}
 *
 * `zalo` has no `requireMention` field and no per-group map at all. That is
 * not a curiosity: OpenClaw's own default is requireMention = TRUE
 * (`dist/group-policy-DPWbhdfw.js`'s resolveChannelGroupRequireMention falls
 * through to `return true`), so an Empyralis owner who turns require_mention
 * OFF for Zalo gets a config that cannot say so and a gateway that keeps
 * mention-gating anyway. See ./openclaw-config-plan.ts for what provisioning
 * does about that (fails the channel closed, loudly — never ships a config
 * that quietly disagrees with the owner's setting).
 */

/** Where a channel keeps its per-conversation overrides, if anywhere.
 *  `groups` for most; `teams` for Microsoft Teams; null when the channel has
 *  no per-conversation map in its schema at all. */
export type OpenClawPerChatMapKey = "groups" | "teams" | null;

export interface OpenClawChannelPolicyShape {
  /** `channels.<id>.dmPolicy`'s enum. Empty set = the channel has no
   *  dmPolicy field at all. */
  readonly dmPolicyModes: readonly string[];
  /** `channels.<id>.groupPolicy`'s enum. */
  readonly groupPolicyModes: readonly string[];
  /** True when `channels.<id>.requireMention` exists (a channel-wide lever). */
  readonly channelRequireMention: boolean;
  /** Which per-conversation map (if any) carries `requireMention`. */
  readonly perChatMapKey: OpenClawPerChatMapKey;
  /**
   * True when the per-conversation map is keyed on the CHAT/GROUP id, so an
   * Empyralis group allowlist (which is a list of chat ids — see
   * personal_channels_service._group_policy_group_id: "NEVER sender_jid")
   * can be written into it verbatim.
   *
   * `groupAllowFrom` is deliberately NOT usable for this: it is a SENDER
   * allowlist ("who may trigger in groups"), a different axis entirely, and
   * writing chat ids into it would produce a config that authorizes nothing
   * while looking configured.
   */
  readonly perChatMapKeyedOnChatId: boolean;
  /** True when `channels.<id>.configWrites` exists — OpenClaw's own switch
   *  for whether IT may write back into the config file (e.g. the in-chat
   *  `/activation` command rewriting `groups.<id>.requireMention`). Turned
   *  off wherever it exists: Empyralis is the sole author of these paths. */
  readonly configWrites: boolean;
}

/** The five channels Empyralis transports through OpenClaw today, keyed by
 *  OpenClaw's OWN channel id — which, by the invariant recorded in
 *  channel_lane_contract_service.OPENCLAW_PERSONAL_CHANNEL_SPECS, is also
 *  exactly the Empyralis `channel_key` minus its `openclaw_` prefix. Must
 *  stay in step with ../capabilities.ts's OPENCLAW_TRANSPORT_CHANNEL_IDS —
 *  asserted by a test, not by hope.
 *
 *  Provisioning is where that invariant stopped being theoretical: writing
 *  `channels.<id>` into a real OpenClaw config is the first operation that
 *  cannot succeed against an id OpenClaw does not have. It caught
 *  `openclaw_qq` (their id is `qqbot`), which was broken in both directions
 *  and silent in both. */
export const OPENCLAW_CHANNEL_POLICY_SHAPES: Readonly<Record<string, OpenClawChannelPolicyShape>> = {
  feishu: {
    dmPolicyModes: ["open", "pairing", "allowlist"],
    groupPolicyModes: ["open", "allowlist", "disabled"],
    channelRequireMention: true,
    perChatMapKey: "groups",
    perChatMapKeyedOnChatId: true,
    configWrites: true,
  },
  line: {
    dmPolicyModes: ["open", "allowlist", "pairing", "disabled"],
    groupPolicyModes: ["open", "allowlist", "disabled"],
    channelRequireMention: false,
    perChatMapKey: "groups",
    perChatMapKeyedOnChatId: true,
    configWrites: false,
  },
  qqbot: {
    dmPolicyModes: ["open", "allowlist", "disabled"],
    groupPolicyModes: ["open", "allowlist", "disabled"],
    channelRequireMention: false,
    perChatMapKey: "groups",
    perChatMapKeyedOnChatId: true,
    configWrites: false,
  },
  zalo: {
    dmPolicyModes: ["pairing", "allowlist", "open", "disabled"],
    groupPolicyModes: ["open", "disabled", "allowlist"],
    channelRequireMention: false,
    perChatMapKey: null,
    perChatMapKeyedOnChatId: false,
    configWrites: false,
  },
  msteams: {
    dmPolicyModes: ["pairing", "allowlist", "open", "disabled"],
    groupPolicyModes: ["open", "disabled", "allowlist"],
    channelRequireMention: true,
    perChatMapKey: "teams",
    perChatMapKeyedOnChatId: true,
    configWrites: true,
  },
};

// ── Schema audit: this table vs the CLI actually installed ────────────────
//
// The table above is a transcription, and a transcription of a moving target
// is precisely the thing CLAUDE.md warns goes stale silently. The version pin
// (./openclaw-version.ts) already refuses a different build — this is the
// second, independent check that the build we DO accept still has the shape
// we assumed, so a same-version repack or a local patch can't quietly change
// what a policy means.

export interface OpenClawChannelShapeFinding {
  channelId: string;
  /** Stable machine code. */
  code:
    | "channel_missing_from_schema"
    | "dm_policy_modes_changed"
    | "group_policy_modes_changed"
    | "require_mention_support_changed"
    | "per_chat_map_changed"
    | "config_writes_support_changed"
    | "unhandled_plugin_hook";
  detail: string;
}

function sortedUnique(values: readonly unknown[]): string[] {
  return [...new Set(values.map((value) => String(value)))].sort();
}

function schemaProperties(node: unknown): Record<string, unknown> {
  if (!node || typeof node !== "object") return {};
  const props = (node as Record<string, unknown>).properties;
  return props && typeof props === "object" ? (props as Record<string, unknown>) : {};
}

/** Reads an enum out of a schema node, tolerating both the plain
 *  `{enum:[...]}` shape and the `{anyOf:[{const:"a"},...]}` shape their
 *  generator emits for some fields (gateway.bind, gateway.mode). */
function schemaEnumValues(node: unknown): string[] | undefined {
  if (!node || typeof node !== "object") return undefined;
  const record = node as Record<string, unknown>;
  if (Array.isArray(record.enum)) return sortedUnique(record.enum);
  const branches = Array.isArray(record.anyOf) ? record.anyOf : Array.isArray(record.oneOf) ? record.oneOf : undefined;
  if (!branches) return undefined;
  const collected: string[] = [];
  for (const branch of branches) {
    if (!branch || typeof branch !== "object") continue;
    const b = branch as Record<string, unknown>;
    if (typeof b.const === "string") collected.push(b.const);
    else if (Array.isArray(b.enum)) collected.push(...b.enum.map(String));
  }
  return collected.length > 0 ? sortedUnique(collected) : undefined;
}

/**
 * Compares OPENCLAW_CHANNEL_POLICY_SHAPES against a live
 * `openclaw config schema` document, for the channels being provisioned.
 *
 * Also answers CHANNEL-ADOPTION-PLAN.md step 4's explicit instruction to
 * "audit every channel you enable for a similar per-channel hook suppression
 * rather than assuming WhatsApp is the only one": rather than hard-coding
 * "only WhatsApp has pluginHooks", it reads every enabled channel's
 * `pluginHooks` object out of the schema and reports any property in it that
 * the caller has not been told to switch on. The caller (./openclaw-config-plan.ts)
 * turns EVERY discovered pluginHooks boolean on, so a future channel that
 * grows the same suppression is covered by construction — and a pluginHooks
 * property that is not a boolean (something we would not know how to set)
 * is reported as a finding instead of guessed at.
 */
export function auditOpenClawChannelShapes(
  schema: unknown,
  channelIds: readonly string[],
): OpenClawChannelShapeFinding[] {
  const findings: OpenClawChannelShapeFinding[] = [];
  const channels = schemaProperties(schemaProperties(schema).channels);

  for (const channelId of channelIds) {
    const expected = OPENCLAW_CHANNEL_POLICY_SHAPES[channelId];
    if (!expected) continue;
    const node = channels[channelId];
    if (!node) {
      findings.push({
        channelId,
        code: "channel_missing_from_schema",
        detail: `channels.${channelId} does not exist in the installed OpenClaw config schema.`,
      });
      continue;
    }
    const props = schemaProperties(node);

    const dmModes = schemaEnumValues(props.dmPolicy) ?? [];
    if (dmModes.join(",") !== sortedUnique(expected.dmPolicyModes).join(",")) {
      findings.push({
        channelId,
        code: "dm_policy_modes_changed",
        detail: `channels.${channelId}.dmPolicy is now [${dmModes.join(", ")}]; the mapping table expects [${sortedUnique(expected.dmPolicyModes).join(", ")}].`,
      });
    }

    const groupModes = schemaEnumValues(props.groupPolicy) ?? [];
    if (groupModes.join(",") !== sortedUnique(expected.groupPolicyModes).join(",")) {
      findings.push({
        channelId,
        code: "group_policy_modes_changed",
        detail: `channels.${channelId}.groupPolicy is now [${groupModes.join(", ")}]; the mapping table expects [${sortedUnique(expected.groupPolicyModes).join(", ")}].`,
      });
    }

    const hasChannelRequireMention = Object.hasOwn(props, "requireMention");
    if (hasChannelRequireMention !== expected.channelRequireMention) {
      findings.push({
        channelId,
        code: "require_mention_support_changed",
        detail: `channels.${channelId}.requireMention is ${hasChannelRequireMention ? "now present" : "no longer present"}; the mapping table assumes the opposite. A require_mention policy may now map differently.`,
      });
    }

    const observedMapKey: OpenClawPerChatMapKey = Object.hasOwn(props, "groups")
      ? "groups"
      : Object.hasOwn(props, "teams")
        ? "teams"
        : null;
    if (observedMapKey !== expected.perChatMapKey) {
      findings.push({
        channelId,
        code: "per_chat_map_changed",
        detail: `channels.${channelId}'s per-conversation map is ${observedMapKey ?? "absent"}; the mapping table expects ${expected.perChatMapKey ?? "absent"}.`,
      });
    }

    const hasConfigWrites = Object.hasOwn(props, "configWrites");
    if (hasConfigWrites !== expected.configWrites) {
      findings.push({
        channelId,
        code: "config_writes_support_changed",
        detail: `channels.${channelId}.configWrites is ${hasConfigWrites ? "now present" : "no longer present"}; the mapping table assumes the opposite.`,
      });
    }
  }

  return findings;
}

/**
 * Every `channels.<id>.pluginHooks.<flag>` boolean the installed schema
 * declares for the given channels. Provisioning sets all of them to true —
 * WhatsApp's `messageReceived` is the one CHANNEL-ADOPTION-PLAN.md names
 * (without it the `message_received` hook is suppressed ENTIRELY and nothing
 * on the Empyralis side can compensate), but nothing here is WhatsApp-
 * specific, so any channel that grows the same switch is handled the day it
 * ships rather than the day someone notices the silence.
 *
 * Returns `{ enable, findings }`: `enable` is the list of dotted paths to set
 * true, `findings` names any pluginHooks property that is NOT a plain boolean
 * — a shape we do not know how to satisfy, and therefore refuse to guess at.
 */
export function resolveOpenClawPluginHookFlags(
  schema: unknown,
  channelIds: readonly string[],
): { enable: Array<{ channelId: string; flag: string }>; findings: OpenClawChannelShapeFinding[] } {
  const enable: Array<{ channelId: string; flag: string }> = [];
  const findings: OpenClawChannelShapeFinding[] = [];
  const channels = schemaProperties(schemaProperties(schema).channels);

  for (const channelId of channelIds) {
    const hooks = schemaProperties(channels[channelId]).pluginHooks;
    if (!hooks) continue;
    for (const [flag, node] of Object.entries(schemaProperties(hooks))) {
      const type = node && typeof node === "object" ? (node as Record<string, unknown>).type : undefined;
      if (type === "boolean") {
        enable.push({ channelId, flag });
        continue;
      }
      findings.push({
        channelId,
        code: "unhandled_plugin_hook",
        detail:
          `channels.${channelId}.pluginHooks.${flag} is not a boolean (type ${String(type)}). ` +
          "Provisioning will not guess a value for it — an un-set plugin hook can suppress the inbound tap " +
          "entirely, which is invisible from the Empyralis side.",
      });
    }
  }

  return { enable, findings };
}

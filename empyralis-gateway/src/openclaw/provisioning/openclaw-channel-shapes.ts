/**
 * What each OpenClaw channel's config can and cannot express about policy.
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
 *
 * THE TABLE IS NO LONGER A TRANSCRIPTION. It was five rows hand-copied out of
 * openclaw@2026.6.10's `openclaw config schema` on 2026-08-08, which covered
 * five of their twenty-seven channels and would have had to be re-copied by
 * hand for every channel added. It is now read out of that same schema by
 * `scripts/generate_openclaw_channel_manifest.py` at generation time and
 * emitted into ../generated-openclaw-channels.ts.
 *
 * The derivation reproduces all five hand-written rows byte-for-byte, which
 * is why it is trusted for the other twenty-two.
 *
 * THE AUDIT BELOW IS STILL A REAL CHECK, not a tautology. Its expected values
 * come from a manifest generated against the PINNED build and checked into
 * the repo; its actual values come from the schema of the OpenClaw actually
 * installed on the customer's box, read at provisioning time. Two different
 * sources — which is the whole point (CLAUDE.md: "a check that derives its
 * own expectations from the thing it checks is blind, and reports 'passed'").
 * A same-version repack or a local patch still cannot quietly change what a
 * policy means.
 */

import {
  GENERATED_OPENCLAW_MANIFEST,
  type GeneratedOpenClawPolicyShape,
} from "../generated-openclaw-channels";

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

function toPolicyShape(shape: GeneratedOpenClawPolicyShape): OpenClawChannelPolicyShape {
  return {
    dmPolicyModes: shape.dm_policy_modes,
    groupPolicyModes: shape.group_policy_modes,
    channelRequireMention: shape.channel_require_mention,
    perChatMapKey: shape.per_chat_map_key,
    perChatMapKeyedOnChatId: shape.per_chat_map_keyed_on_chat_id,
    configWrites: shape.config_writes,
  };
}

/**
 * Every channel the pinned OpenClaw declares a `channels.<id>` config node
 * for, keyed by OpenClaw's OWN channel id — which IS the Empyralis
 * `channel_key` minus its `openclaw_` prefix, an invariant that is now
 * automatic because both sides of it are generated from the same registry.
 *
 * A channel with NO entry here is not an oversight: four catalogued channels
 * (`wecom`, `openclaw-weixin`, `openclaw-zaloclawbot`, `yuanbao`) contribute
 * their config node only once their plugin is installed.
 *
 * WHAT THIS TABLE CANNOT ANSWER, and the bug that came of assuming it could:
 * whether a channel is absent HERE. The four above are the channels that had
 * no node ON THE BOX THE MANIFEST WAS GENERATED FROM — a per-version snapshot,
 * while plugin installs are per-machine and change under it in both
 * directions. The live answer is `resolveOpenClawChannelKeySupport`'s
 * `hasConfigNode`, read from the schema of the OpenClaw actually installed.
 *
 * This comment used to say `renderOpenClawConfig` "writes `{enabled: false}`
 * and reports a finding, so those degrade honestly". That was the bug: an id
 * with no config namespace is refused OUTRIGHT (`unknown channel id`), so
 * `{enabled: false}` is not a safe minimum — it is the same refusal in a
 * smaller costume, and it took every other channel on the box down with it.
 *
 * Provisioning is where the verbatim-id invariant stopped being theoretical:
 * writing `channels.<id>` into a real OpenClaw config is the first operation
 * that cannot succeed against an id OpenClaw does not have. It is what caught
 * `openclaw_qq` (their id is `qqbot`), broken in both directions and silent in
 * both.
 */
export const OPENCLAW_CHANNEL_POLICY_SHAPES: Readonly<Record<string, OpenClawChannelPolicyShape>> =
  Object.freeze(
    Object.fromEntries(
      GENERATED_OPENCLAW_MANIFEST.channels
        .filter((channel) => channel.policy_shape !== null)
        .map((channel) => [channel.id, toPolicyShape(channel.policy_shape!)]),
    ),
  );

if (Object.keys(OPENCLAW_CHANNEL_POLICY_SHAPES).length === 0) {
  // Without shapes, renderOpenClawConfig disables EVERY channel and reports it
  // as a policy finding — a total, plausible-looking outage of the transport.
  // Fail at load instead.
  throw new Error(
    "No OpenClaw channel policy shapes were generated. Regenerate with " +
      "`python3 scripts/generate_openclaw_channel_manifest.py`.",
  );
}

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
    | "unhandled_plugin_hook"
    // A `channels.<id>.tools.<flag>` provisioning cannot switch off. Only
    // reachable once a channel PLUGIN is installed — plugins contribute their
    // own tool surface, and the global `tools.*` lockdown does not reach it.
    | "unhandled_channel_tool";
  detail: string;
}

function sortedUnique(values: readonly unknown[]): string[] {
  return [...new Set(values.map((value) => String(value)))].sort();
}

/** The one JSON-Schema walker in this tree. Exported so the credential-shape
 *  derivation (./openclaw-channel-credential-shape.ts) reads the same document
 *  the same way rather than growing a second one beside it. */
export function schemaProperties(node: unknown): Record<string, unknown> {
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
 * Also answers the OpenClaw channel adoption step 4's explicit instruction to
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

// ── Which top-level keys a channel node will actually ACCEPT ──────────────
//
// WHY THIS IS A SEPARATE QUESTION FROM THE POLICY SHAPE ABOVE
// -----------------------------------------------------------
// `openclaw config patch` VALIDATES, and almost every `channels.<id>` node is
// `additionalProperties: false`. So a key the node does not declare is not
// ignored — it refuses the write. And because provisioning pushes the whole
// document as ONE patch ("every channel, always, never a partial push"), one
// unacceptable key on one channel refuses EVERY channel on that box.
//
// That is not hypothetical. It is what a real provisioning run against a real
// openclaw@2026.6.10 returned:
//
//   openclaw_config_patch_failed
//   "Config validation failed: channels.clickclack: invalid config:
//    must not have additional properties: "groupPolicy", "dmPolicy""
//
// ClickClack has neither field (it hardcodes `dmPolicy: "allowlist"` /
// `groupPolicy: "allowlist"` in its own ingress call and exposes only
// `allowFrom`), and the generator wrote both anyway — so no channel could be
// provisioned on any box, Telegram included.
//
// The POLICY SHAPE above already answers presence for `dmPolicy`,
// `groupPolicy`, `requireMention`, `groups`/`teams` and `configWrites` — its
// enums are empty and its booleans false exactly when the field is absent, and
// auditOpenClawChannelShapes cross-checks every one of those against the
// installed schema before a single byte is written. What it does NOT carry is
// `enabled` and `allowFrom`, because the manifest's `policy_shape` has no field
// for them. Those two are read here, from the installed schema, the same way
// resolveOpenClawPluginHookFlags and resolveOpenClawChannelToolFlags already
// read theirs — DISCOVERED, never listed, so a channel added upstream tomorrow
// is covered without an Empyralis edit.
//
// One source per key, deliberately: nothing below re-answers a question the
// policy shape already answers, so the two can never drift into disagreeing
// about the same field.

/** The top-level `channels.<id>.*` keys renderOpenClawConfig can write. Used
 *  to pick a branch out of an `anyOf` node — see below. */
export const OPENCLAW_CHANNEL_WRITABLE_KEYS: readonly string[] = [
  "enabled",
  "dmPolicy",
  "allowFrom",
  "groupPolicy",
  "requireMention",
  "groups",
  "teams",
  "configWrites",
];

// ── Where a channel keeps its DM policy and its SENDER ALLOWLIST ──────────
//
// WHY THIS IS DISCOVERED PER CHANNEL RATHER THAN ASSUMED TO BE `allowFrom`
// ------------------------------------------------------------------------
// It was assumed, and the assumption cost a real box its whole provisioning
// run. Measured against a real openclaw@2026.6.10, on the founder's own Mac:
//
//   openclaw.provision.refused
//   openclaw_security_audit_not_clean
//   channels.synology-chat.warning.3 [critical]
//     Synology Chat: dmPolicy="allowlist" with empty allowedUserIds blocks all
//     senders. Add users or set dmPolicy="open" with allowedUserIds=["*"].
//
// Synology Chat's sender list is `allowedUserIds`. This module wrote
// `allowFrom: ["*"]` into it — and the write SUCCEEDED, because that node is
// the one channel node in the pinned build whose `additionalProperties` is
// permissive (`{}`), so an undeclared key is accepted rather than refused.
// Accepted, and then never read: the channel kept its own default
// (`dmPolicy: "allowlist"` with an empty `allowedUserIds`), which admits
// nobody, which their audit correctly calls critical — and because the push is
// all-or-nothing, that one channel refused all of them, Telegram included.
//
// THE RULE THAT FOLLOWS, and it is the general one: **a node ACCEPTING a key
// is not evidence the channel READS it.** `acceptsUndeclaredKeys` answers
// "will this write be refused", never "will this write do anything", and the
// second question is the one an authorization setting has to pass. So the DM
// keys are now taken from what the node DECLARES, and a channel that declares
// none gets none written — reported as a channel whose direct-message policy
// Empyralis cannot express, exactly like the other axes that cannot be
// expressed.
//
// THREE SHAPES EXIST IN THE PINNED BUILD, and only the first was handled:
//
//   flat     channels.<id>.dmPolicy   + channels.<id>.allowFrom      19 channels
//   nested   channels.<id>.dm.policy  + channels.<id>.dm.allowFrom   matrix, googlechat
//   neither  nothing declared at all                                 synology-chat, tlon, …
//
// The nested pair is not a different setting, it is the same setting moved one
// level down — same enum, same array, same `"*"` contract — so it is written
// where it exists rather than reported as absent, which is what used to happen
// (both channels landed in `dm_policy_not_expressible_no_lever` and got no DM
// policy at all).
//
// DERIVED, NEVER CHANNEL-LISTED: the table below names KEY paths, not
// channels, and every entry is checked against the installed schema per
// channel — the same discipline OPENCLAW_CHANNEL_WRITABLE_KEYS already uses.
// A channel that moves upstream tomorrow is covered if it moves to a shape
// listed here and is REPORTED, not guessed at, if it does not.
//
// The derivation is cross-checked against a SECOND, INDEPENDENT source in the
// test suite: `openclaw channels capabilities --json` makes each plugin state
// its own `setupWizard.dmPolicy.policyKey` / `.allowFromKey` as fully-qualified
// config paths. That is the plugin's own answer, from plugin metadata rather
// than from the config schema, so the expected set and the actual set do not
// come from one place (CLAUDE.md: "a check that derives its own expectations
// from the thing it checks is blind, and reports 'passed'").
// ORDER IS OPENCLAW'S OWN PRECEDENCE, not a preference of ours. Four channel
// nodes in the pinned build declare BOTH shapes (discord, slack — neither
// transported, both superseded by a first-party runtime — plus the two nested
// ones, which declare no flat pair), and their resolver breaks the tie
// explicitly:
//
//   dist/bundled-channel-config-schema-*.js
//   account.dmPolicy ?? account.dm?.policy ?? value.dmPolicy ?? value.dm?.policy
//
// i.e. FLAT WINS. Writing the nested pair on a channel that declares both
// would produce a document OpenClaw reads the other half of — configured, and
// ignored, which is the same class of defect as writing an undeclared key.
const OPENCLAW_DM_SURFACE_LOCATIONS: ReadonlyArray<{
  /** Path segments from `channels.<id>` to the object holding the pair. */
  readonly container: readonly string[];
  readonly policyKey: string;
  readonly allowlistKey: string;
}> = [
  { container: [], policyKey: "dmPolicy", allowlistKey: "allowFrom" },
  { container: ["dm"], policyKey: "policy", allowlistKey: "allowFrom" },
];

/**
 * Where ONE channel keeps its direct-message policy, as its own installed
 * schema declares it. Paths are segments relative to `channels.<id>`.
 *
 * `null` on either path means the channel has no such field — not that it has
 * one somewhere we did not look. Writing an authorization setting into a key
 * a channel does not read produces a config that looks configured and
 * authorizes nothing, which is strictly worse than writing nothing and saying
 * so.
 */
export interface OpenClawChannelDmSurface {
  readonly policyPath: readonly string[] | null;
  /** The enum at `policyPath`. Empty exactly when `policyPath` is null. */
  readonly policyModes: readonly string[];
  readonly allowlistPath: readonly string[] | null;
}

const NO_DM_SURFACE: OpenClawChannelDmSurface = Object.freeze({
  policyPath: null,
  policyModes: [],
  allowlistPath: null,
});

/** True for a schema node that declares an array — the shape every one of
 *  OpenClaw's sender allowlists has. A non-array under an allowlist name is
 *  something we do not know how to fill, so it does not count as one. */
function isArrayNode(node: unknown): boolean {
  return Boolean(node) && typeof node === "object" && (node as Record<string, unknown>).type === "array";
}

function resolveDmSurface(node: unknown): OpenClawChannelDmSurface {
  const props = schemaProperties(node);
  for (const location of OPENCLAW_DM_SURFACE_LOCATIONS) {
    const container = location.container.length === 0 ? node : props[location.container[0]];
    if (!container) continue;
    const containerProps = schemaProperties(container);
    const policyModes = Object.hasOwn(containerProps, location.policyKey)
      ? schemaEnumValues(containerProps[location.policyKey]) ?? []
      : [];
    const hasAllowlist = isArrayNode(containerProps[location.allowlistKey]);
    if (policyModes.length === 0 && !hasAllowlist) continue;
    return {
      policyPath: policyModes.length > 0 ? [...location.container, location.policyKey] : null,
      policyModes,
      allowlistPath: hasAllowlist ? [...location.container, location.allowlistKey] : null,
    };
  }
  return NO_DM_SURFACE;
}

export interface OpenClawChannelKeySupport {
  readonly channelId: string;
  /**
   * Whether THIS BOX's OpenClaw has a `channels.<id>` config namespace at all.
   *
   * A DIFFERENT FAILURE from "declared, but nothing on it is writable" — and it
   * has to be, because the two need opposite documents. An undeclared KEY on a
   * declared channel is refused with `must not have additional properties`; an
   * undeclared CHANNEL is refused with `unknown channel id`, and there is no
   * block small enough to get past it. Not `{enabled: false}`, not `{}`.
   *
   * THE RULE, read out of their validator (dist/io-*.js) and then measured:
   *
   *   allowed = bundled channel-config metadata WHERE configurable !== false
   *           ∪ channel ids contributed by INSTALLED plugins
   *
   * so `openclaw config schema`'s `channels.properties` keys are the same set
   * plus the `configurable: false` entries, which is why this is read off the
   * schema rather than guessed. Measured on a bare profile
   * (`config patch --dry-run`, one probe, five ids):
   *
   *   channels.openclaw-weixin:      unknown channel id: openclaw-weixin
   *   channels.openclaw-zaloclawbot: unknown channel id: openclaw-zaloclawbot
   *   channels.wecom:                unknown channel id: wecom
   *   channels.yuanbao:              unknown channel id: yuanbao
   *   channels.qa-channel:           unknown channel id: qa-channel
   *   channels.telegram:             (accepted)
   *
   * Three things that rule is NOT, each of which looks right and is wrong:
   *
   *   `channels list --all --json` -> `installed`   ALL 27 report false on a
   *     bare profile, yet 23 of them are perfectly writable. Installation is
   *     what a channel needs to CARRY TRAFFIC, not to be configured.
   *   presence in `dist/extensions/`                only 7 chat channels are on
   *     disk; the config metadata for 24 is compiled into the binary.
   *   a schema node existing                        `qa-channel` has a full
   *     schema node and `configurable: false`, so it is refused anyway. It is
   *     the ONLY such entry in the pinned build, and it is absent from their
   *     channel registry, so no Empyralis channel can ever be it — but that is
   *     a fact worth an assertion, not an assumption (see the test).
   */
  readonly hasConfigNode: boolean;
  /** The keys from OPENCLAW_CHANNEL_WRITABLE_KEYS this node declares. */
  readonly keys: readonly string[];
  /**
   * True when the node's `additionalProperties` is anything other than
   * `false` — an undeclared key is then ACCEPTED rather than refused.
   * `synology-chat` is the live example (`additionalProperties: {}`).
   *
   * ACCEPTED IS NOT READ, and conflating the two is what took a real box's
   * whole provisioning run down (see OPENCLAW_DM_SURFACE_LOCATIONS above).
   * This flag therefore governs `enabled` ONLY — a switch a permissive node
   * genuinely honours, verified against a live instance — and never an
   * authorization setting, whose key must be one the channel DECLARES.
   */
  readonly acceptsUndeclaredKeys: boolean;
  /** Where this channel's direct-message policy and sender allowlist live,
   *  read off its own node. Never assumed to be `allowFrom`. */
  readonly dm: OpenClawChannelDmSurface;
}

interface BranchKeySupport {
  keys: string[];
  acceptsUndeclaredKeys: boolean;
  dm: OpenClawChannelDmSurface;
  /** False when the branch demands a property provisioning neither writes nor
   *  can leave to a default — writing ANY key against such a branch refuses
   *  the whole document. */
  writable: boolean;
}

function branchKeySupport(node: unknown): BranchKeySupport {
  const props = schemaProperties(node);
  const record = node && typeof node === "object" ? (node as Record<string, unknown>) : {};
  const keys = OPENCLAW_CHANNEL_WRITABLE_KEYS.filter((key) => Object.hasOwn(props, key));

  // `required` is not academic. Thirteen channel nodes in the pinned build
  // declare one (feishu names eight properties, whatsapp four), and every
  // patch validated anyway because each of those carries a `default` that the
  // merged document picks up. `twitch` is the one that does not:
  //
  //   Error: Config validation failed: channels.twitch.username:
  //          invalid config: must have required property 'username'
  //
  // — measured, from the real CLI, while verifying this very fix. So a
  // required property is satisfiable only if provisioning writes it or OpenClaw
  // defaults it; anything else makes the branch unusable, and a channel with no
  // usable branch is one Empyralis cannot configure at all rather than one it
  // may configure badly.
  const required = Array.isArray(record.required) ? record.required.map(String) : [];
  const writable = required.every(
    (name) =>
      keys.includes(name) ||
      Object.hasOwn((props[name] ?? {}) as Record<string, unknown>, "default"),
  );

  return {
    keys,
    dm: resolveDmSurface(node),
    // Absent `additionalProperties` defaults to permissive in JSON Schema, but
    // every channel node in the pinned build states it explicitly. Treating an
    // ABSENT one as permissive would be the fail-open direction on the exact
    // question this function exists to answer, so it is treated as `false`
    // unless the node says otherwise.
    acceptsUndeclaredKeys: Object.hasOwn(record, "additionalProperties") && record.additionalProperties !== false,
    writable,
  };
}

/**
 * Which of the writable keys each channel's node will accept.
 *
 * `anyOf`/`oneOf` nodes (twitch is the live one: a credential branch and an
 * `accounts` branch) are resolved by picking the SINGLE branch that declares
 * the most writable keys, ties broken by declaration order. A union across
 * branches would be unsound — a document mixing keys from two branches matches
 * NEITHER and the patch is refused — so exactly one branch has to be chosen,
 * and the widest is the only choice that cannot lose a lever a narrower branch
 * would also have given us.
 */
export function resolveOpenClawChannelKeySupport(
  schema: unknown,
  channelIds: readonly string[],
): OpenClawChannelKeySupport[] {
  const channels = schemaProperties(schemaProperties(schema).channels);
  const support: OpenClawChannelKeySupport[] = [];

  for (const channelId of channelIds) {
    const node = channels[channelId];
    // NO NODE AT ALL = this box's OpenClaw does not have this channel's config
    // namespace, so `channels.<id>` is not a key it will accept in any form —
    // not even `{enabled: false}`. Reported as its own fact rather than skipped;
    // see hasConfigNode's own comment for why this is a different failure from
    // "declared, but nothing writable on it".
    if (!node) {
      support.push({
        channelId,
        hasConfigNode: false,
        keys: [],
        acceptsUndeclaredKeys: false,
        dm: NO_DM_SURFACE,
      });
      continue;
    }
    const record = node as Record<string, unknown>;
    const branches = Array.isArray(record.anyOf)
      ? record.anyOf
      : Array.isArray(record.oneOf)
        ? record.oneOf
        : undefined;

    const candidates = (branches ?? [node]).map(branchKeySupport).filter((candidate) => candidate.writable);
    // No usable branch: say so by declaring nothing writable, which
    // renderOpenClawConfig reports as `channel_not_configurable` and leaves
    // alone. `twitch` is the live case — both of its branches demand
    // credentials, so there is no document Empyralis can write for it at all.
    const best: BranchKeySupport =
      candidates.length === 0
        ? { keys: [], acceptsUndeclaredKeys: false, writable: false, dm: NO_DM_SURFACE }
        : candidates.reduce((widest, candidate) => (candidate.keys.length > widest.keys.length ? candidate : widest));

    support.push({
      channelId,
      hasConfigNode: true,
      keys: best.keys,
      acceptsUndeclaredKeys: best.acceptsUndeclaredKeys,
      dm: best.dm,
    });
  }

  return support;
}

/**
 * Every `channels.<id>.pluginHooks.<flag>` boolean the installed schema
 * declares for the given channels. Provisioning sets all of them to true —
 * WhatsApp's `messageReceived` is the one the OpenClaw channel adoption names
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

/**
 * Every `channels.<id>.tools.<flag>` boolean the installed schema declares.
 * Provisioning sets all of them to FALSE.
 *
 * WHY THIS EXISTS, AND WHY IT COULD ONLY APPEAR IN STEP 5
 * -------------------------------------------------------
 * The lockdown's tool posture is GLOBAL — `tools.profile: "minimal"`,
 * `tools.elevated.enabled: false`, `tools.fs.workspaceOnly`, plus an explicit
 * `tools.deny`. A CHANNEL PLUGIN can contribute its own per-channel tool
 * surface that none of those cover, and until provisioning started installing
 * channel plugins there was no plugin present to contribute one.
 *
 * `@openclaw/feishu` does: `channels.feishu.tools` carries doc / chat / wiki /
 * drive / perm / scopes / bitable / base — create documents, manage
 * permissions, reach Drive. Their own audit flags it the moment a credential
 * is configured, which is exactly how this was found:
 *
 *   channels.feishu.doc_owner_open_id [warn]
 *   "channels.feishu tools include \"doc\"; feishu_doc action \"create\" can
 *    grant document access to the trusted requesting Feishu user."
 *   remediation: "Disable channels.feishu.tools.doc when not needed…"
 *
 * A transport instance has no agent and therefore no legitimate use for any of
 * it, and OpenClaw's own trust model is explicit that anyone who can message a
 * tool-enabled agent shares that agent's authority. An instance carrying a
 * customer's contacts' messages must not also be able to create documents in
 * their company's Feishu tenant.
 *
 * DISCOVERED, never listed: hard-coding the eight flags Feishu ships today
 * would silently stop covering the ninth, and would cover nothing at all for
 * the next plugin that grows a `tools` node. Same reason
 * resolveOpenClawPluginHookFlags discovers rather than special-cases WhatsApp.
 *
 * A non-boolean under `tools` is a FINDING, not a guess — and a shape finding
 * refuses the whole provisioning run, which is the right outcome for "this
 * channel has a tool surface we do not know how to switch off".
 */
export function resolveOpenClawChannelToolFlags(
  schema: unknown,
  channelIds: readonly string[],
): { disable: Array<{ channelId: string; flag: string }>; findings: OpenClawChannelShapeFinding[] } {
  const disable: Array<{ channelId: string; flag: string }> = [];
  const findings: OpenClawChannelShapeFinding[] = [];
  const channels = schemaProperties(schemaProperties(schema).channels);

  for (const channelId of channelIds) {
    const tools = schemaProperties(channels[channelId]).tools;
    if (!tools) continue;
    for (const [flag, node] of Object.entries(schemaProperties(tools))) {
      const type = node && typeof node === "object" ? (node as Record<string, unknown>).type : undefined;
      if (type === "boolean") {
        disable.push({ channelId, flag });
        continue;
      }
      findings.push({
        channelId,
        code: "unhandled_channel_tool",
        detail:
          `channels.${channelId}.tools.${flag} is not a boolean (type ${String(type)}), so provisioning cannot ` +
          "switch it off. A transport instance must carry NO tool authority; refusing rather than leaving a " +
          "channel-contributed tool surface enabled on a machine we do not own.",
      });
    }
  }

  return { disable, findings };
}

/**
 * Every channel id the schema-audit / plugin-hook / tool-flag discovery
 * passes must scan (MAN-367).
 *
 * THE GAP THIS CLOSES
 * -------------------
 * `auditOpenClawChannelShapes`, `resolveOpenClawPluginHookFlags` and
 * `resolveOpenClawChannelToolFlags` all take a `channelIds` list and were
 * built, tested and wired against exactly one source of it: the curated
 * `plan.channels` (the ~20-27 channels this codebase authors a
 * `EmpyralisChannelPolicy` for). That was every channel plugin that existed
 * when tool-flag discovery shipped.
 *
 * The REGISTRY (ClawHub) channel-plugin install path shipped later and is a
 * second way for a channel's plugin to land on a box — `ensureRegistryPluginsInstalled`
 * (./openclaw-plugin-install.ts) resolves a channel id ONLY AFTER install,
 * because a third-party plugin's channel id is not knowable in advance
 * (`OpenClawRegistryPluginState.revealedChannelIds`). Nothing threaded those
 * revealed ids into the `channelIds` passed to the three functions above, so
 * a ClawHub-installed channel's `channels.<id>.tools.*` node — the exact
 * per-channel tool surface `resolveOpenClawChannelToolFlags`'s own docstring
 * says the global `tools.*` lockdown does not reach — was never discovered
 * and never forced off, on any run, forever (the curated list never grows to
 * include a registry channel; there is no "next run picks it up").
 *
 * Of the 112 community ClawHub plugins the derived manifest carries as of
 * this writing, 44 (39%) are flagged `scan_status: "suspicious"` by
 * OpenClaw's own trust scanner — installing one is a real, sanctioned
 * product action (`POST .../openclaw/gateways/{id}/provision` with
 * `install_plugins`, "member" role) even though nothing in the frontend
 * calls it today. This function is the fix: union the curated ids with every
 * id a registry install revealed this run, so the SAME discovery this
 * codebase already trusts for Feishu et al. runs against a ClawHub-sourced
 * channel too. `auditOpenClawChannelShapes` is safe to widen this way
 * without new false refusals — it silently `continue`s past any channelId
 * absent from `OPENCLAW_CHANNEL_POLICY_SHAPES`, which every registry channel
 * is, so it gains no new failure mode; the tool/hook flag functions have no
 * such gate and discover generically from the live schema, which is exactly
 * the coverage that was missing.
 *
 * Deliberately NOT a trust filter and does not touch which plugins may be
 * installed or listed — that catalog is intentionally uncurated
 * (`test_community_plugins_are_carried_not_filtered_out`, the founder's own
 * "whatever OpenClaw offers as a channel, Empyralis offers" rule). This only
 * makes the tool-authority lockdown this codebase already claims to run
 * actually reach every channel that lands on the box, curated or not.
 */
export function schemaAuditChannelIds(
  planChannelIds: readonly string[],
  registryStates: readonly { revealedChannelIds: readonly string[] }[],
): string[] {
  return [
    ...new Set([...planChannelIds, ...registryStates.flatMap((state) => state.revealedChannelIds)]),
  ];
}

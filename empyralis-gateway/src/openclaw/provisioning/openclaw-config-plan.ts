/**
 * PURE. Empyralis's stored policy -> the OpenClaw config document that
 * enforces it, plus the security lockdown, plus a fingerprint. No filesystem,
 * no child process, no network — every rule below is testable without an
 * OpenClaw install.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * WHY THIS FILE IS THE POINT OF STEP 4
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Step 2 established from their shipped source that OpenClaw's own config is
 * a SECOND AUTHORIZATION POLICY STORE, and that it runs FIRST:
 *
 *     inbound
 *        |
 *        v
 *     decideChannelIngress()          <-- OpenClaw's config decides here
 *        |  admission drop|skip|pairing-required  => "block-dispatch"
 *        |                                           never enqueued
 *        v
 *     message_received hook           <-- our tap
 *        v
 *     Empyralis's three gates         <-- our config decides here
 *
 * So Empyralis's gates can only ever NARROW what OpenClaw already admitted.
 * If OpenClaw's config is not generated from Empyralis's stored policy, the
 * owner's Empyralis settings stop describing reality — CLAUDE.md's "silent
 * misrouting beats loud failure" failure mode, the one that already cost this
 * product a banned account.
 *
 * ─── THE AUTHORITY TABLE (this is the whole design) ──────────────────────
 *
 * The correct relationship between the two stores is NOT "make them
 * identical". It is per-axis, and it falls straight out of which gate facts
 * survive OpenClaw's tap (step 2, verified against the shipped bundle):
 *
 *   AXIS               FACT AT OUR TAP     WHO IS AUTHORITATIVE   OPENCLAW MUST BE
 *   ---------------    ----------------    --------------------   ----------------
 *   dm sender policy   sender id: PRESENT  Empyralis re-decides   ⊇ (never stricter)
 *   group chat policy  chat id: PRESENT    Empyralis re-decides   ⊇ (never stricter)
 *   require_mention    wasMentioned: GONE  OpenClaw ONLY          == (exact)
 *
 * Two directions of divergence, only one of them is a bug:
 *
 *   OpenClaw LOOSER than Empyralis -> messages arrive and Empyralis drops
 *     them with a named reason. Settings still describe reality. Acceptable,
 *     but only deliberately: every widening is recorded (see `widenings`)
 *     and reported, never inferred by a reader.
 *
 *   OpenClaw STRICTER than Empyralis -> messages vanish BEFORE our process
 *     exists. Empyralis's settings say "open"; the customer sees silence;
 *     nothing anywhere logs why. THIS is the bug. It is unrecoverable by
 *     construction, because we never learn the message existed.
 *
 * `require_mention` is the one axis with no ⊇ escape: OpenClaw's tap forwards
 * neither `isGroup` nor `wasMentioned` (message-hook-mappers-*.js forwards
 * both ONLY on the sibling `inbound_claim` event), so Empyralis can never
 * conclude "was mentioned" and can therefore never re-enforce a
 * require_mention=false decision on OpenClaw's behalf. It must be exact in
 * their config or it is not enforced as configured anywhere.
 *
 * ─── WHAT HAPPENS WHEN IT CANNOT BE EXACT ────────────────────────────────
 *
 * `channels.zalo` has no `requireMention` field and no per-conversation map
 * at all (./openclaw-channel-shapes.ts), while OpenClaw's own default is
 * requireMention = TRUE (dist/group-policy-*.js's
 * resolveChannelGroupRequireMention falls through to `return true`). So an
 * owner who sets require_mention=false for Zalo in Empyralis cannot have that
 * honoured — and cannot be told by anything downstream, because the dropped
 * messages never reach us.
 *
 * Provisioning FAILS THAT CHANNEL CLOSED: `channels.zalo.enabled = false`,
 * with a stable code and a plain sentence, surfaced to the cloud. The channel
 * is off and the owner is told why. It is never enabled with a policy that
 * quietly disagrees with what they set. Same posture step 2 took with unknown
 * group-ness: when the honest answer is unavailable, take the strict side and
 * say so.
 *
 * ─── A THIRD CASE: THE AXIS HAS NO FIELD AT ALL ──────────────────────────
 *
 * The table above assumes both stores can at least SAY the thing. Nine of the
 * pinned build's twenty-three channel nodes cannot:
 *
 *   clickclack  no dmPolicy, no groupPolicy   allowFrom is the only lever
 *   synology    no dmPolicy, no groupPolicy   (additionalProperties, permissive)
 *   tlon        no dmPolicy, no groupPolicy, no allowFrom
 *   twitch      no writable branch at all     every branch demands a credential
 *   matrix      no dmPolicy, no allowFrom     both moved under a nested `dm`
 *   googlechat  no dmPolicy, no allowFrom     "
 *   nostr/sms   no groupPolicy
 *   feishu      dmPolicy without "disabled"   a mode outside the enum, same class
 *
 * Writing a key a node does not declare is not ignored — `openclaw config
 * patch` VALIDATES, almost every node is `additionalProperties: false`, and
 * this module pushes the whole document as ONE patch. So one unwritable key on
 * one channel refuses EVERY channel on the box. Measured, from a real
 * provisioning run through the product's own route:
 *
 *   openclaw_config_patch_failed — "Config validation failed:
 *   channels.clickclack: invalid config: must not have additional
 *   properties: \"groupPolicy\", \"dmPolicy\""
 *
 * — which is why no channel, Telegram included, could be provisioned on any
 * box. The rule that follows: NEVER write a key or a mode the channel's own
 * schema does not carry, and never state a policy the owner did not choose
 * just to have something to write. The axis is left on OpenClaw's default and
 * the omission is RECORDED with its own code — a different fact from the
 * widenings above ("we chose something looser" vs "nobody could choose"), and
 * one that would be a lie if the two shared a code.
 *
 * Where an omission would be WORSE than nothing, nothing is written at all:
 * `dmPolicy: "open"` without the `allowFrom` wildcard means DROP EVERY DM, and
 * `channels.<id>: {}` is a present object that gets validated like any other
 * (`channels.twitch: {}` is refused for a missing `username`), so an empty
 * block is omitted from the document rather than written as `{}`.
 *
 * ─── DRIFT ────────────────────────────────────────────────────────────────
 *
 * The generated document is a DERIVED ARTIFACT of Empyralis's policy, in the
 * same category as `empyralis-runtime-kernel`'s compiled binary (CLAUDE.md,
 * MAN-306): built from a source of truth, never itself a source of truth, and
 * silently wrong the moment the two disagree. `fingerprintOpenClawConfig`
 * below makes "was this built from the current policy" a single comparable
 * value. ./openclaw-provisioner.ts decides what to do about a mismatch; see
 * its doc comment.
 */

import crypto from "crypto";

import {
  OPENCLAW_CHANNEL_POLICY_SHAPES,
  type OpenClawChannelKeySupport,
  type OpenClawChannelPolicyShape,
} from "./openclaw-channel-shapes";

// ── Empyralis's side of the vocabulary ───────────────────────────────────
//
// Deliberately the SAME names/values personal_channels_service.py stores, so
// the cloud hands its policy over verbatim and nothing translates twice.

export type EmpyralisDmPolicyMode = "owner_only" | "allowlist" | "pairing" | "open";
export type EmpyralisGroupPolicyMode = "open" | "allowlist" | "disabled";

export interface EmpyralisDmPolicy {
  mode: EmpyralisDmPolicyMode;
  /** Sender ids, in whatever spelling arrived from OpenClaw in the first
   *  place (they get into this list by an owner approving a pairing request
   *  whose sender id came off an OpenClaw message), so they round-trip. */
  allowlist: string[];
}

export interface EmpyralisGroupPolicy {
  mode: EmpyralisGroupPolicyMode;
  /** CHAT ids — never sender ids. personal_channels_service.
   *  _group_policy_group_id: "this is NEVER sender_jid". */
  allowlist: string[];
  requireMention: boolean;
}

export interface EmpyralisChannelPolicy {
  /** OpenClaw's own channel id — the Empyralis `channel_key` minus its
   *  `openclaw_` prefix. See openclaw-channel-shapes.ts. */
  channelId: string;
  /** Whether the owner wants this channel carrying traffic at all. A channel
   *  the owner has not connected is simply absent from the plan. */
  enabled: boolean;
  dmPolicy: EmpyralisDmPolicy;
  groupPolicy: EmpyralisGroupPolicy;
  /**
   * Whether this box should acquire the channel's PLUGIN, not just its policy.
   *
   * Separate from `enabled` on purpose. Policy is written for every channel on
   * every run — a channel omitted from the config would keep whatever the last
   * run left, which is the stale-derived-artifact failure provisioning exists
   * to prevent — but twenty of the twenty-seven channels are separate npm
   * packages, and installing all of them on every customer's machine to write
   * a policy nobody asked for is minutes of network per boot and twenty
   * third-party dependencies running inside an instance that carries their
   * messages.
   *
   * So the two questions are asked separately: "what may this channel do"
   * (always answered) and "does this box need this channel's code at all"
   * (answered only for channels the owner has actually reached for). The cloud
   * decides the second — see openclaw_provisioning_service.py.
   *
   * Absent/false is NOT "this channel is broken": it is "no plugin here", and
   * it is reported as exactly that (`OpenClawChannelPluginState.installed`).
   */
  installPlugin?: boolean;
}

export interface OpenClawProvisioningPlan {
  /** `--profile <name>` -> ~/.openclaw-<name>. One per customer instance;
   *  never shared. Their trust model is explicitly single-operator. */
  profile: string;
  /** The loopback port OpenClaw's own gateway listens on. */
  gatewayPort: number;
  /** Absolute path to `~/.openclaw-<profile>` — this instance's own state
   *  directory, used to pin the agent workspace inside it. See the
   *  `agents.defaults.workspace` note in renderOpenClawConfig. */
  profileStateDir: string;
  /** Absolute path to the Empyralis bridge plugin directory. */
  bridgePluginPath: string;
  /** Extra plugin load paths (verification drivers, never in production). */
  extraPluginPaths?: string[];
  channels: EmpyralisChannelPolicy[];
  /** Every `channels.<id>.pluginHooks.<flag>` the installed schema declares,
   *  discovered by resolveOpenClawPluginHookFlags — all set to true. */
  pluginHookFlags?: Array<{ channelId: string; flag: string }>;
  /** Every `channels.<id>.tools.<flag>` the installed schema declares,
   *  discovered by resolveOpenClawChannelToolFlags — all set to FALSE. A
   *  channel plugin's own tool surface is not covered by the global `tools.*`
   *  lockdown, and a transport instance carries no tool authority. */
  channelToolFlags?: Array<{ channelId: string; flag: string }>;
  /** Which top-level `channels.<id>.*` keys each channel's node will ACCEPT,
   *  discovered by resolveOpenClawChannelKeySupport from the installed schema.
   *  Answers `enabled` and `allowFrom` only — every other key this file writes
   *  is answered by the channel's policy shape, which auditOpenClawChannelShapes
   *  has already cross-checked against that same schema.
   *
   *  Absent (or missing an entry for a channel) means "assume acceptable",
   *  which is what this file did before key support existed. The two keys it
   *  covers are present on every channel in the pinned build EXCEPT
   *  `allowFrom` on matrix/googlechat/tlon, so a caller that omits it is not
   *  silently reintroducing the ClickClack refusal — that one is closed by the
   *  policy shape, which is always available. */
  channelKeySupport?: readonly OpenClawChannelKeySupport[];
  /** Channel-plugin ids this box has actually installed, from OpenClaw's own
   *  install registry (./openclaw-plugin-install.ts). They join the bridge
   *  plugin in `plugins.allow`, so the instance's plugin inventory is an
   *  explicit list rather than whatever happens to be on disk. */
  installedChannelPluginIds?: readonly string[];
}

/** Secrets are passed separately from the plan so the plan itself can be
 *  journaled, hashed, diffed, and shipped to the cloud without ever carrying
 *  a token. The rendered config DOES contain the gateway token (OpenClaw
 *  needs it), which is why renderOpenClawConfig returns it separately from
 *  the fingerprinted policy document — see below. */
export interface OpenClawSecrets {
  /** OpenClaw's own gateway auth token (`gateway.auth.token`). */
  gatewayToken: string;
}

// ── Findings ──────────────────────────────────────────────────────────────

export type OpenClawPolicyFindingCode =
  /** The owner's policy cannot be expressed and Empyralis cannot re-enforce
   *  it either. The channel is disabled. */
  | "require_mention_off_not_expressible"
  /** The channel's node will not even accept `enabled`, so provisioning
   *  cannot switch it on or off. Nothing is written for it at all. */
  | "channel_not_configurable"
  /** Expressible only more loosely than Empyralis's own policy. Safe (we
   *  re-decide), but recorded rather than assumed. */
  | "dm_pairing_widened_to_open"
  | "dm_owner_only_widened_to_open"
  | "dm_allowlist_mode_unavailable_widened_to_open"
  | "group_allowlist_not_keyed_by_chat_widened_to_open"
  // ── The axis has no field on this channel at all ────────────────────────
  //
  // A DIFFERENT FACT from the four above, and deliberately not folded into
  // them. Those four say "we deliberately wrote something looser than your
  // setting, and Empyralis re-decides that axis". These say "there is no
  // field to write, so OpenClaw's OWN default governs and Empyralis cannot
  // vouch that it is not stricter than what you chose". Both end up in
  // `widenings` because both are safe-enough-to-enable, but a reader who
  // cannot tell them apart cannot tell "we chose this" from "nobody chose
  // this".
  /** No `dmPolicy` field, but `allowFrom` exists — the sender list is the only
   *  lever, and it was written. */
  | "dm_policy_mode_not_expressible_sender_list_only"
  /** Neither `dmPolicy` nor `allowFrom`. Nothing was written for direct
   *  messages; Empyralis enforces the whole policy itself. */
  | "dm_policy_not_expressible_no_lever"
  /** `dmPolicy` exists but cannot be set to the mode Empyralis needs, and the
   *  wildcard sender list that makes "open" mean open is unavailable. Writing
   *  `dmPolicy: "open"` without it would DROP EVERY DM — the unrecoverable
   *  direction — so neither key is written. */
  | "dm_policy_open_requires_wildcard_allowlist"
  /** No `groupPolicy` field. OpenClaw's own group default governs; Empyralis
   *  applies the owner's group policy itself on every message that arrives. */
  | "group_policy_not_expressible"
  /** `groupPolicy` exists but not the mode Empyralis needs, so it is set to
   *  the widest mode the channel does have. */
  | "group_policy_mode_unavailable_widened_to_open";

export interface OpenClawPolicyFinding {
  channelId: string;
  code: OpenClawPolicyFindingCode;
  /** One plain sentence. This renders in front of an owner (step 8), so no
   *  schema paths, no jargon. */
  detail: string;
}

export interface RenderedOpenClawConfig {
  /** The full config document to hand to `openclaw config patch`. Contains
   *  the gateway token; never journal this object. */
  config: Record<string, unknown>;
  /** The same document with every secret replaced by a fixed placeholder.
   *  Safe to journal, ship to the cloud, and diff. This is what
   *  fingerprintOpenClawConfig hashes, so rotating a token is not mistaken
   *  for a policy change — and a policy change is never mistaken for
   *  nothing. */
  redactedConfig: Record<string, unknown>;
  fingerprint: string;
  /** Channels this provisioning run refuses to enable, with the reason. */
  disabledChannels: OpenClawPolicyFinding[];
  /** Deliberate loosenings of OpenClaw's side relative to Empyralis's, each
   *  safe only because Empyralis re-decides that axis itself. */
  widenings: OpenClawPolicyFinding[];
}

const REDACTED = "<redacted>";

/** The bridge plugin's own id, as declared in its `openclaw.plugin.json`. It
 *  is both an `entries` key and an `allow` entry, and the two disagreeing
 *  would silently unload the one plugin the whole transport depends on. */
export const OPENCLAW_BRIDGE_PLUGIN_ID = "empyralis-bridge";

// ── The security lockdown ─────────────────────────────────────────────────
//
// CHANNEL-ADOPTION-PLAN.md's SECURITY LOCKDOWN section, as code rather than
// prose, so a future edit to the generator that drops one of these fails a
// test instead of shipping. Context for why none of it is negotiable: tens of
// thousands of OpenClaw gateways were found internet-exposed leaking API keys
// and chat history. Not a code flaw — operators exposed them. Shipping it to
// customers means owning that on their behalf.

export interface OpenClawLockdownViolation {
  path: string;
  code: string;
  detail: string;
}

/** The exact values every provisioned instance must have. Checked against the
 *  EFFECTIVE config read back from OpenClaw itself (not against what we
 *  intended to write) — see ./openclaw-provisioner.ts. */
export const OPENCLAW_LOCKDOWN_EXPECTATIONS: ReadonlyArray<{
  path: string;
  expected: unknown;
  code: string;
  detail: string;
}> = [
  {
    path: "gateway.mode",
    expected: "local",
    code: "gateway_mode_not_local",
    detail: "OpenClaw refuses to start without gateway.mode 'local' (\"suspicious or clobbered config\").",
  },
  {
    path: "gateway.bind",
    expected: "loopback",
    code: "gateway_bind_not_loopback",
    detail: "The OpenClaw gateway must bind loopback only. Anything else puts a customer's chat history and tokens on their LAN.",
  },
  {
    path: "gateway.auth.mode",
    expected: "token",
    code: "gateway_auth_mode_not_token",
    detail: "Gateway auth must be token, always — including on loopback. gateway.auth.mode must never be 'none'.",
  },
  {
    path: "discovery.mdns.mode",
    expected: "off",
    code: "mdns_not_off",
    detail: "Bonjour/mDNS is ON BY DEFAULT and advertised itself on the LAN unprompted during testing. It stays off.",
  },
  {
    path: "gateway.controlUi.enabled",
    expected: false,
    code: "control_ui_enabled",
    detail: "OpenClaw's own Control UI is a second, unowned admin surface on the customer's machine. Empyralis is the UI.",
  },
  {
    path: "gateway.tailscale.mode",
    expected: "off",
    code: "tailscale_publish_enabled",
    detail: "Tailscale serve/funnel would publish this gateway beyond the box. Off.",
  },
  {
    path: "gateway.remote.enabled",
    expected: false,
    code: "remote_gateway_enabled",
    detail: "A remote gateway link would move the trust boundary off this machine.",
  },
  {
    path: "discovery.wideArea.enabled",
    expected: false,
    code: "wide_area_discovery_enabled",
    detail: "Wide-area discovery publishes gateway presence beyond the local link.",
  },
  // ── Tool authority: none. ───────────────────────────────────────────────
  //
  // Added 2026-08-08 because `openclaw security audit` caught it on the FIRST
  // live provisioning run and refused the instance — two CRITICAL findings
  // (`security.exposure.open_groups_with_elevated`,
  // `security.exposure.open_groups_with_runtime_or_fs`) against a config that
  // otherwise passed every check written here. That is the audit gate paying
  // for itself, and the right response was to make the finding untrue, never
  // to soften the gate.
  //
  // The reasoning is independent of the audit: this instance is a RADIO. Its
  // brain is deliberately off, so it has no legitimate use for exec, process,
  // filesystem, UI, node, cron, or gateway tools — and OpenClaw's own trust
  // model is explicit that anyone who can message a tool-enabled agent shares
  // that agent's delegated tool authority. An instance that carries a
  // customer's contacts' messages must not also be able to run commands on
  // their machine.
  {
    path: "tools.profile",
    expected: "minimal",
    code: "tool_profile_not_minimal",
    detail: "A transport instance runs the smallest tool baseline; it has no agent to use tools.",
  },
  {
    path: "tools.elevated.enabled",
    expected: false,
    code: "elevated_tools_enabled",
    detail: "Elevated tool access plus an inbound channel is how a prompt injection becomes a host incident.",
  },
  {
    path: "tools.fs.workspaceOnly",
    expected: true,
    code: "fs_tools_not_workspace_only",
    detail: "Filesystem tools must never reach outside the workspace on a machine we do not own.",
  },
];

/** Tool groups and individual tools this deployment denies outright. The
 *  group names are OpenClaw's own (docs/channels/groups.md's sandbox example);
 *  the individual names are exactly the ones their exposure audit checks
 *  (`collectRiskyToolExposureContexts`: exec/process, read/write/edit/apply_patch),
 *  denied by name too so the posture does not depend on what a profile happens
 *  to include this release. */
export const OPENCLAW_DENIED_TOOLS: readonly string[] = [
  "group:runtime",
  "group:fs",
  "group:ui",
  "nodes",
  "cron",
  "gateway",
  "exec",
  "process",
  "read",
  "write",
  "edit",
  "apply_patch",
];

/** Env vars that would hand OpenClaw a model provider credential behind our
 *  back. The deliberate "brain off" design depends on EVERY agent turn
 *  failing fast with `FailoverError: No API key found` — the bridge plugin's
 *  `message_sending` predicate suppresses exactly that reply. One inherited
 *  key and OpenClaw starts answering the customer's contacts itself, in its
 *  own voice, with its own memory, from a config nobody chose. Stripped from
 *  the child environment at spawn, which is stronger than configuring them
 *  empty: there is no value to drift. */
export const OPENCLAW_FORBIDDEN_CREDENTIAL_ENV_PATTERNS: readonly RegExp[] = [
  /^ANTHROPIC_/,
  /^OPENAI_/,
  /^AZURE_OPENAI_/,
  /^GOOGLE_(API_KEY|GENAI|APPLICATION_CREDENTIALS)/,
  /^GEMINI_/,
  /^GROQ_/,
  /^MISTRAL_/,
  /^COHERE_/,
  /^DEEPSEEK_/,
  /^XAI_/,
  /^TOGETHER_/,
  /^FIREWORKS_/,
  /^PERPLEXITY_/,
  /^OPENROUTER_/,
  /^AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY|SESSION_TOKEN|BEARER_TOKEN_BEDROCK)/,
  /^OLLAMA_/,
  /^CLAUDE_CODE_/,
  /_API_KEY$/,
];

/** Removes every model-provider credential from an environment before
 *  OpenClaw is spawned into it. Returns a NEW object; never mutates. */
export function sanitizeOpenClawChildEnv(env: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const clean: NodeJS.ProcessEnv = {};
  for (const [key, value] of Object.entries(env)) {
    if (OPENCLAW_FORBIDDEN_CREDENTIAL_ENV_PATTERNS.some((pattern) => pattern.test(key))) continue;
    clean[key] = value;
  }
  return clean;
}

/** The credential env vars present in `env` — reported (by NAME only, never
 *  by value) so "why is my OpenClaw answering by itself" is answerable, and
 *  so a box whose operator exported a key learns that we stripped it. */
export function detectForbiddenCredentialEnvNames(env: NodeJS.ProcessEnv): string[] {
  return Object.keys(env)
    .filter((key) => OPENCLAW_FORBIDDEN_CREDENTIAL_ENV_PATTERNS.some((pattern) => pattern.test(key)))
    .sort();
}

function readPath(document: unknown, path: string): unknown {
  let cursor: unknown = document;
  for (const segment of path.split(".")) {
    if (!cursor || typeof cursor !== "object") return undefined;
    cursor = (cursor as Record<string, unknown>)[segment];
  }
  return cursor;
}

/**
 * Checks an EFFECTIVE OpenClaw config (read back from the instance, not the
 * one we meant to write) against the lockdown, plus the two invariants that
 * are not single-value comparisons:
 *
 *   - no model provider may carry a credential (brain off);
 *   - `security.audit.suppressions` must be empty, or "the audit is clean"
 *     stops meaning anything.
 */
export function findOpenClawLockdownViolations(effectiveConfig: unknown): OpenClawLockdownViolation[] {
  const violations: OpenClawLockdownViolation[] = [];

  for (const expectation of OPENCLAW_LOCKDOWN_EXPECTATIONS) {
    const actual = readPath(effectiveConfig, expectation.path);
    if (actual !== expectation.expected) {
      violations.push({
        path: expectation.path,
        code: expectation.code,
        detail: `${expectation.detail} (found: ${JSON.stringify(actual)})`,
      });
    }
  }

  const token = readPath(effectiveConfig, "gateway.auth.token");
  if (typeof token !== "string" || token.trim().length === 0) {
    violations.push({
      path: "gateway.auth.token",
      code: "gateway_auth_token_missing",
      detail: "Gateway auth is set to token mode with no token — that is an unauthenticated gateway with extra steps.",
    });
  }

  const providers = readPath(effectiveConfig, "models.providers");
  if (providers && typeof providers === "object") {
    for (const [providerId, entry] of Object.entries(providers as Record<string, unknown>)) {
      const apiKey = entry && typeof entry === "object" ? (entry as Record<string, unknown>).apiKey : undefined;
      if (apiKey !== undefined && apiKey !== null && apiKey !== "") {
        violations.push({
          path: `models.providers.${providerId}.apiKey`,
          code: "model_provider_credential_configured",
          detail:
            `A model provider credential is configured for "${providerId}". This deployment runs OpenClaw as a ` +
            "transport with its brain deliberately off: every agent turn must fail fast so the bridge plugin can " +
            "suppress the reply. With a credential present, OpenClaw answers the customer's contacts itself.",
        });
      }
    }
  }

  const deny = readPath(effectiveConfig, "tools.deny");
  const denied = new Set(Array.isArray(deny) ? deny.map(String) : []);
  const missingDenies = OPENCLAW_DENIED_TOOLS.filter((tool) => !denied.has(tool));
  if (missingDenies.length > 0) {
    violations.push({
      path: "tools.deny",
      code: "tool_denylist_incomplete",
      detail:
        `These tools are not denied on a transport-only instance: ${missingDenies.join(", ")}. ` +
        "Anyone who can message this gateway shares whatever tool authority its agent has, and this one is " +
        "supposed to have none.",
    });
  }

  // ── Plugin inventory: an explicit allowlist, never "whatever is on disk" ──
  //
  // Added 2026-08-09, when provisioning started INSTALLING channel plugins and
  // OpenClaw itself said so on the very first run:
  //
  //   [plugins] plugins.allow is empty; discovered non-bundled plugins may
  //   auto-load: feishu (…/node_modules/@openclaw/feishu/dist/index.js).
  //   Set plugins.allow to explicit trusted ids.
  //
  // With it empty, ANY package that reaches the profile's plugin directory
  // loads into the instance that carries a customer's messages — and that
  // directory now has a writer. Their own semantics make the allowlist the
  // right control rather than a blunt one: "when set, only listed plugins are
  // eligible to load. Configured bundled chat channels can still activate
  // their bundled plugin when the channel is explicitly enabled in config" —
  // and this generator always writes an explicit `channels.<id>` block, so
  // bundled channels are unaffected. Verified live on a provisioned instance:
  // the bridge plugin, the installed Feishu plugin and bundled `irc` all stay
  // `loaded`, while `telegram` (not enabled) stays `disabled`.
  //
  // The VALUE is per-box (it names the plugins that box installed), so it
  // cannot be an OPENCLAW_LOCKDOWN_EXPECTATIONS constant. Its exact contents
  // are still enforced by the read-back drift check, which requires every
  // generated path to match; what is asserted here is the part that is
  // invariant — that it is set at all.
  const allow = readPath(effectiveConfig, "plugins.allow");
  if (!Array.isArray(allow) || allow.length === 0) {
    violations.push({
      path: "plugins.allow",
      code: "plugin_allowlist_empty",
      detail:
        "No plugin allowlist is in force, so any plugin package present on disk is eligible to load into this " +
        "instance. Provisioning installs channel plugins into this profile, which means that directory has a " +
        "writer; an unbounded inventory there is unreviewed third-party code running beside a customer's messages.",
    });
  }

  const suppressions = readPath(effectiveConfig, "security.audit.suppressions");
  if (Array.isArray(suppressions) && suppressions.length > 0) {
    violations.push({
      path: "security.audit.suppressions",
      code: "security_audit_suppressions_present",
      detail:
        `${suppressions.length} security-audit suppression(s) are configured. Provisioning treats a non-clean ` +
        "audit as a failure, which is worth nothing if findings can be silenced in the same config.",
    });
  }

  return violations;
}

// ── Policy -> config ──────────────────────────────────────────────────────

interface RenderedChannel {
  channelId: string;
  block: Record<string, unknown>;
  disabled?: OpenClawPolicyFinding;
  widenings: OpenClawPolicyFinding[];
}

/**
 * Whether a top-level `channels.<id>.<key>` may be written at all.
 *
 * ONLY `enabled` and `allowFrom` go through here. Every other key this file
 * writes is gated on the channel's POLICY SHAPE — an empty `dmPolicyModes` IS
 * "this channel has no dmPolicy field", `perChatMapKey: null` IS "no groups
 * map", and auditOpenClawChannelShapes refuses the whole run if the installed
 * schema and that shape ever disagree. Routing those through a second source
 * would be two answers to one question.
 */
function channelKeyWriter(
  channelId: string,
  keySupport: readonly OpenClawChannelKeySupport[] | undefined,
): (key: "enabled" | "allowFrom") => boolean {
  const entry = keySupport?.find((candidate) => candidate.channelId === channelId);
  if (!entry) return () => true;
  return (key) => entry.acceptsUndeclaredKeys || entry.keys.includes(key);
}

function renderChannel(
  policy: EmpyralisChannelPolicy,
  shape: OpenClawChannelPolicyShape,
  keySupport: readonly OpenClawChannelKeySupport[] | undefined,
): RenderedChannel {
  const widenings: OpenClawPolicyFinding[] = [];
  const block: Record<string, unknown> = {};
  const canWrite = channelKeyWriter(policy.channelId, keySupport);

  // ── Can this channel be switched at all? ────────────────────────────────
  //
  // Before anything else, including the `enabled: false` shortcut: a node that
  // refuses `enabled` refuses `{enabled: false}` just as hard, and one refused
  // key refuses the entire push for every channel on the box.
  if (!canWrite("enabled")) {
    return {
      channelId: policy.channelId,
      block: {},
      widenings,
      disabled: {
        channelId: policy.channelId,
        code: "channel_not_configurable",
        detail:
          `This channel is left alone because OpenClaw's ${policy.channelId} channel does not accept an on/off ` +
          "switch from Empyralis. Configuring it from here would be refused outright, and refusing it would take " +
          "every other channel on this computer down with it.",
      },
    };
  }

  if (!policy.enabled) {
    return { channelId: policy.channelId, block: { enabled: false }, widenings };
  }

  // ── Axis 1: require_mention. EXACT or fail closed. ─────────────────────
  //
  // Order matters: this is decided FIRST, because a channel that cannot
  // honour it is not enabled at all and the other two axes are then moot.
  const wantMention = policy.groupPolicy.requireMention;
  const groupsAreOff = policy.groupPolicy.mode === "disabled";
  const groupAllowlistEmpty =
    policy.groupPolicy.mode === "allowlist" && policy.groupPolicy.allowlist.length === 0;
  // When no group message can be admitted at all, mention gating is
  // unreachable and therefore trivially "exact" whatever it is set to.
  const mentionAxisReachable = !groupsAreOff && !groupAllowlistEmpty;

  if (mentionAxisReachable && !wantMention) {
    const canExpressMentionOff = shape.channelRequireMention || shape.perChatMapKey !== null;
    if (!canExpressMentionOff) {
      return {
        channelId: policy.channelId,
        block: { enabled: false },
        widenings,
        disabled: {
          channelId: policy.channelId,
          code: "require_mention_off_not_expressible",
          detail:
            `This channel is turned off because OpenClaw cannot carry the setting you chose. You asked the agent ` +
            `to reply in groups without being mentioned, but OpenClaw's ${policy.channelId} channel has no way to ` +
            "switch mention-gating off, and it requires a mention by default. Turning the channel on anyway would " +
            "mean your setting says one thing and the channel does another, with the ignored messages never " +
            "reaching Empyralis at all. Turn \"only reply when mentioned\" back on for this channel to use it.",
        },
      };
    }
  }

  // ── Axis 2: group chat policy. Empyralis re-decides; OpenClaw ⊇. ───────
  //
  // `groupTarget` is what Empyralis's policy MEANS in OpenClaw's vocabulary.
  // Whether that word can actually be written is a second question, answered
  // below against the channel's own enum — the two were fused before, which is
  // how `groupPolicy` came to be written to a channel that has no such field.
  let groupTarget: EmpyralisGroupPolicyMode;
  if (policy.groupPolicy.mode === "disabled") {
    groupTarget = "disabled";
  } else if (policy.groupPolicy.mode === "open") {
    groupTarget = "open";
  } else if (policy.groupPolicy.allowlist.length === 0) {
    // An allowlist with nothing in it admits nothing. Saying "disabled" is
    // the exact same policy stated in the vocabulary OpenClaw actually has,
    // and it is the DEFAULT state of a freshly-bound agent
    // (DEFAULT_GROUP_POLICY_MODE = allowlist, allowlist = []).
    groupTarget = "disabled";
  } else if (shape.perChatMapKeyedOnChatId && shape.perChatMapKey) {
    groupTarget = "allowlist";
  } else {
    // The channel has no chat-id-keyed map. `groupAllowFrom` exists but is a
    // SENDER allowlist — a different axis — so writing chat ids into it would
    // authorize nothing while looking configured. Widen to open and let
    // Empyralis's own chat-id gate (which still sees the chat id) decide.
    groupTarget = "open";
    widenings.push({
      channelId: policy.channelId,
      code: "group_allowlist_not_keyed_by_chat_widened_to_open",
      detail:
        `OpenClaw's ${policy.channelId} channel cannot express a per-chat allowlist, so it is set to accept group ` +
        "messages and Empyralis applies your allowed-chats list itself. Your setting is still enforced.",
    });
  }

  // An EMPTY enum is the shape's way of saying the field does not exist —
  // clickclack, nostr, sms, tlon and twitch all reach this. A mode outside a
  // non-empty enum is the same class of defect one level down: `disabled`
  // written to a channel that only has [allowlist, open] would be refused
  // just as loudly.
  if (shape.groupPolicyModes.length === 0) {
    widenings.push({
      channelId: policy.channelId,
      code: "group_policy_not_expressible",
      detail:
        `OpenClaw's ${policy.channelId} channel has no setting for group chats, so it uses its own default and ` +
        "Empyralis applies your group policy itself on every message that reaches it. Your setting is still " +
        "enforced for anything the agent can see.",
    });
  } else if (shape.groupPolicyModes.includes(groupTarget)) {
    block.groupPolicy = groupTarget;
  } else if (shape.groupPolicyModes.includes("open")) {
    // Never the reverse: narrowing to a mode the owner did not choose is the
    // direction where messages vanish before Empyralis exists.
    block.groupPolicy = "open";
    widenings.push({
      channelId: policy.channelId,
      code: "group_policy_mode_unavailable_widened_to_open",
      detail:
        `OpenClaw's ${policy.channelId} channel has no "${groupTarget}" setting for group chats, so it is set to ` +
        "accept them and Empyralis applies your group policy itself. Your setting is still enforced.",
    });
  } else {
    widenings.push({
      channelId: policy.channelId,
      code: "group_policy_not_expressible",
      detail:
        `OpenClaw's ${policy.channelId} channel has no "${groupTarget}" setting for group chats and no wider one ` +
        "either, so it uses its own default and Empyralis applies your group policy itself. Your setting is still " +
        "enforced for anything the agent can see.",
    });
  }

  // The per-conversation map carries BOTH the chat allowlist (its keys) and
  // the mention lever (its entries). Built once, from both. Keyed off
  // `groupTarget` rather than off what was written: a channel whose map exists
  // but whose groupPolicy field does not must still get the mention lever, or
  // enabling it on the strength of that map (see canExpressMentionOff above)
  // would be enabling it on a lever nothing ever pulled.
  if (shape.perChatMapKey && shape.perChatMapKeyedOnChatId && groupTarget !== "disabled") {
    const entries: Record<string, unknown> = {};
    if (policy.groupPolicy.mode === "allowlist") {
      for (const chatId of [...policy.groupPolicy.allowlist].sort()) {
        entries[chatId] = { requireMention: wantMention };
      }
    } else {
      // groupPolicy "open": one wildcard entry carries the mention lever for
      // every chat. NOTE their resolveChannelGroupPolicy: a `"*"` key makes
      // allowAll true, i.e. every group is allowed — which is what "open"
      // means, so the two agree rather than fight.
      entries["*"] = { requireMention: wantMention };
    }
    if (Object.keys(entries).length > 0) block[shape.perChatMapKey] = entries;
  }

  // Channel-wide lever too, where it exists: belt and braces, and it is the
  // one msteams actually reads (its resolver prefers channelConfig over
  // teamConfig).
  if (shape.channelRequireMention) block.requireMention = wantMention;

  // ── Axis 3: DM sender policy. Empyralis re-decides; OpenClaw ⊇. ────────
  //
  // `dmPolicy: "open"` ALONE MEANS "DROP EVERY DM". Their own config
  // validator says it out loud — `channels.<id>.dmPolicy="open" but
  // channels.<id>.allowFrom does not include "*"; all DMs will be dropped` —
  // and their startup checks repeat it per channel ("dmPolicy=open with empty
  // allowedUserIds blocks all senders"). "open" is the POLICY MODE; the
  // wildcard in `allowFrom` is what actually admits anyone.
  //
  // That is the unrecoverable direction of divergence (OpenClaw stricter than
  // Empyralis): every DM would vanish before our tap, with Empyralis's
  // settings screen still saying the channel is open, and nothing anywhere
  // able to report the drop. Found by running provisioning against a real
  // openclaw@2026.6.10 and reading its config warnings — not by reading the
  // schema, which says nothing about it. Hence openWithWildcard() below: this
  // module never writes "open" without the wildcard that makes it mean open.
  //
  // Two questions again, and fusing them is the same defect the group axis
  // had: `dmMode`/`dmAllowFrom` below say what Empyralis's policy MEANS, and
  // `writeDm` decides what of that the channel will actually accept.
  let dmMode: "open" | "allowlist" = "open";
  let dmAllowFrom: string[] = ["*"];
  const openWithWildcard = (): void => {
    dmMode = "open";
    dmAllowFrom = ["*"];
  };
  switch (policy.dmPolicy.mode) {
    case "open":
      openWithWildcard();
      break;
    case "allowlist":
      if (shape.dmPolicyModes.includes("allowlist")) {
        dmMode = "allowlist";
        dmAllowFrom = [...policy.dmPolicy.allowlist].sort();
      } else {
        openWithWildcard();
        widenings.push({
          channelId: policy.channelId,
          code: "dm_allowlist_mode_unavailable_widened_to_open",
          detail:
            `OpenClaw's ${policy.channelId} channel has no sender allowlist, so it accepts direct messages and ` +
            "Empyralis applies your allowed-senders list itself. Your setting is still enforced.",
        });
      }
      break;
    case "pairing":
      // NOT mapped to OpenClaw's own `pairing`, deliberately. Theirs blocks
      // dispatch with admission "pairing-required" and runs its own approval
      // store (`openclaw pairing approve`) that an Empyralis owner has no
      // access to. Our pairing challenge is a REPLY we send to the unknown
      // sender — it can only be sent if the message reaches us. Mapping
      // pairing->pairing would leave both flows dead and the sender silent.
      openWithWildcard();
      widenings.push({
        channelId: policy.channelId,
        code: "dm_pairing_widened_to_open",
        detail:
          "Direct messages from new people reach Empyralis, which sends them the pairing request and waits for " +
          "your approval. OpenClaw's own pairing is deliberately not used — it would block the message before " +
          "Empyralis could ever send that request.",
      });
      break;
    case "owner_only":
      // OpenClaw has no owner_only. The tempting mapping — allowlist
      // containing the owner's channel id — is STRICTER than intended the
      // moment that id is stale or spelled differently, and stricter is the
      // unrecoverable direction. Empyralis knows who the owner is and
      // re-decides on the sender id, which survives the tap intact.
      openWithWildcard();
      widenings.push({
        channelId: policy.channelId,
        code: "dm_owner_only_widened_to_open",
        detail:
          "OpenClaw has no \"only me\" setting for direct messages, so Empyralis enforces it: messages from anyone " +
          "else reach Empyralis and are dropped there. Your setting is still enforced.",
      });
      break;
  }

  // Now express it, or say why it cannot be.
  const canWriteAllowFrom = canWrite("allowFrom");
  if (shape.dmPolicyModes.length === 0) {
    // No `dmPolicy` field at all — clickclack, matrix, googlechat, tlon.
    // ClickClack is the instructive one: it hardcodes `dmPolicy: "allowlist"`
    // in its own ingress call and exposes `allowFrom` as the single lever, so
    // the sender list IS the policy there and writing it is exact rather than
    // a compromise. We cannot know that from the schema for every channel,
    // which is why this is recorded rather than assumed.
    if (canWriteAllowFrom) {
      block.allowFrom = dmAllowFrom;
      widenings.push({
        channelId: policy.channelId,
        code: "dm_policy_mode_not_expressible_sender_list_only",
        detail:
          `OpenClaw's ${policy.channelId} channel has no direct-message policy setting, only a list of allowed ` +
          "senders, so that list is what gets written and Empyralis applies the rest of your setting itself.",
      });
    } else {
      widenings.push({
        channelId: policy.channelId,
        code: "dm_policy_not_expressible_no_lever",
        detail:
          `OpenClaw's ${policy.channelId} channel has no direct-message settings Empyralis can write, so it uses ` +
          "its own defaults and Empyralis applies your direct-message policy itself on every message that reaches " +
          "it. Your setting is still enforced for anything the agent can see.",
      });
    }
  } else if (!shape.dmPolicyModes.includes(dmMode)) {
    // Every non-empty dm enum in the pinned build carries "open", so this is
    // the guard for a build that changes rather than a branch anyone reaches
    // today. There is no narrower fallback on offer: narrowing is the
    // direction where messages vanish before Empyralis exists.
    widenings.push({
      channelId: policy.channelId,
      code: "dm_policy_not_expressible_no_lever",
      detail:
        `OpenClaw's ${policy.channelId} channel has no "${dmMode}" setting for direct messages, so it uses its own ` +
        "default and Empyralis applies your direct-message policy itself. Your setting is still enforced for " +
        "anything the agent can see.",
    });
  } else if (!canWriteAllowFrom) {
    // BOTH modes are meaningless without the sender list. `dmPolicy: "open"`
    // ALONE MEANS DROP EVERY DM (see above), and `"allowlist"` with no list
    // means the same thing more obviously. Writing the mode would be strictly
    // worse than writing nothing.
    widenings.push({
      channelId: policy.channelId,
      code: "dm_policy_open_requires_wildcard_allowlist",
      detail:
        `OpenClaw's ${policy.channelId} channel has no list of allowed senders, so its direct-message setting is ` +
        "left alone rather than set to a value that would silently drop every message. Empyralis applies your " +
        "direct-message policy itself.",
    });
  } else {
    block.dmPolicy = dmMode;
    block.allowFrom = dmAllowFrom;
  }

  block.enabled = true;
  // OpenClaw writing back into its own config file (the in-chat `/activation`
  // command rewrites groups.<id>.requireMention) would be drift in a document
  // Empyralis is the sole author of. Off wherever the field exists.
  if (shape.configWrites) block.configWrites = false;

  return { channelId: policy.channelId, block, widenings };
}

/** Recursively sorts object keys so the same policy always produces
 *  byte-identical JSON. Determinism is what makes the fingerprint mean
 *  anything. */
function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === "object") {
    const sorted: Record<string, unknown> = {};
    for (const key of Object.keys(value as Record<string, unknown>).sort()) {
      sorted[key] = canonicalize((value as Record<string, unknown>)[key]);
    }
    return sorted;
  }
  return value;
}

export function canonicalJson(value: unknown): string {
  return JSON.stringify(canonicalize(value));
}

/** sha256 over the canonical, SECRET-FREE document. Rotating the gateway
 *  token must not read as a policy change, and a policy change must never
 *  read as nothing. */
export function fingerprintOpenClawConfig(redactedConfig: unknown): string {
  return crypto.createHash("sha256").update(canonicalJson(redactedConfig), "utf8").digest("hex");
}

/**
 * The whole document, deterministically, from the plan.
 *
 * Everything Empyralis owns is written explicitly, including values that
 * happen to match OpenClaw's current defaults — a default is not a promise,
 * and this config must still say what it means after they change one.
 */
export function renderOpenClawConfig(
  plan: OpenClawProvisioningPlan,
  secrets: OpenClawSecrets,
): RenderedOpenClawConfig {
  const disabledChannels: OpenClawPolicyFinding[] = [];
  const widenings: OpenClawPolicyFinding[] = [];
  const channels: Record<string, unknown> = {};

  for (const policy of [...plan.channels].sort((a, b) => a.channelId.localeCompare(b.channelId))) {
    const shape = OPENCLAW_CHANNEL_POLICY_SHAPES[policy.channelId];
    if (!shape) {
      // A channel id with no transcribed shape is a channel whose policy
      // semantics we have not read. Never guess at an authorization mapping.
      disabledChannels.push({
        channelId: policy.channelId,
        code: "require_mention_off_not_expressible",
        detail:
          `Empyralis has no verified policy mapping for OpenClaw's "${policy.channelId}" channel, so it is left ` +
          "off rather than configured from a guess.",
      });
      channels[policy.channelId] = { enabled: false };
      continue;
    }
    const rendered = renderChannel(policy, shape, plan.channelKeySupport);
    // An EMPTY block is not written as `channels.<id>: {}` — it is left out
    // altogether. `{}` is not "say nothing about this channel": it is a
    // present object, and OpenClaw validates it like any other. `channels
    // .twitch: {}` is refused ("must have required property 'username'"),
    // which would take the whole push down for a channel we had already
    // decided not to touch.
    if (Object.keys(rendered.block).length > 0) channels[policy.channelId] = rendered.block;
    if (rendered.disabled) disabledChannels.push(rendered.disabled);
    widenings.push(...rendered.widenings);
  }

  // Per-channel plugin-hook switches, discovered from the installed schema
  // (never a hard-coded "only WhatsApp"). Merged into whatever block the
  // policy loop produced for that channel.
  for (const { channelId, flag } of plan.pluginHookFlags ?? []) {
    const existing = (channels[channelId] as Record<string, unknown> | undefined) ?? {};
    const hooks = (existing.pluginHooks as Record<string, unknown> | undefined) ?? {};
    channels[channelId] = { ...existing, pluginHooks: { ...hooks, [flag]: true } };
  }

  // Per-channel tool surfaces, all OFF. Discovered from the installed schema
  // (never a hard-coded list), because a channel PLUGIN contributes these and
  // the global `tools.*` lockdown does not reach them — `channels.feishu.tools`
  // would otherwise let anyone who can message this transport create documents
  // and grant permissions in the owner's Feishu tenant.
  for (const { channelId, flag } of plan.channelToolFlags ?? []) {
    const existing = (channels[channelId] as Record<string, unknown> | undefined) ?? {};
    const tools = (existing.tools as Record<string, unknown> | undefined) ?? {};
    channels[channelId] = { ...existing, tools: { ...tools, [flag]: false } };
  }

  const pluginPaths = [plan.bridgePluginPath, ...(plan.extraPluginPaths ?? [])];
  // Extra plugin paths exist ONLY for live verification (the synthetic
  // inbound driver that stands in for real channel credentials until step 5).
  // A plugin whose path is loaded but whose entry is not enabled does not run,
  // so the entry is derived from the directory name — the same id convention
  // every plugin in this repo uses. Production plans never set this.
  const extraEntries: Record<string, unknown> = {};
  for (const extraPath of plan.extraPluginPaths ?? []) {
    const id = extraPath.replace(/\/+$/, "").split("/").pop();
    if (id) extraEntries[id] = { enabled: true };
  }

  const base: Record<string, unknown> = {
    gateway: {
      mode: "local",
      port: plan.gatewayPort,
      bind: "loopback",
      auth: { mode: "token", token: secrets.gatewayToken },
      controlUi: { enabled: false },
      tailscale: { mode: "off" },
      remote: { enabled: false },
    },
    discovery: {
      mdns: { mode: "off" },
      wideArea: { enabled: false },
    },
    plugins: {
      enabled: true,
      load: { paths: pluginPaths },
      // The instance's entire plugin inventory, named. Sorted and deduped so
      // the fingerprint does not move on argument order — a config that
      // reports "changed" on every run trains everyone to ignore the signal.
      allow: [
        ...new Set([
          OPENCLAW_BRIDGE_PLUGIN_ID,
          ...Object.keys(extraEntries),
          ...(plan.installedChannelPluginIds ?? []),
        ]),
      ].sort(),
      entries: {
        ...extraEntries,
        [OPENCLAW_BRIDGE_PLUGIN_ID]: { enabled: true },
        // Bonjour is a separate plugin from discovery.mdns and defaults on.
        bonjour: { enabled: false },
      },
    },
    // No providers, ever. Written explicitly as an empty object rather than
    // omitted: `openclaw config patch` merges objects recursively, so an
    // empty object leaves a previously-written provider in place, while this
    // plus the lockdown read-back (findOpenClawLockdownViolations) means a
    // credential that appears from anywhere fails the instance closed. The
    // env is the other half — sanitizeOpenClawChildEnv.
    models: { providers: {} },
    tools: {
      profile: "minimal",
      elevated: { enabled: false },
      fs: { workspaceOnly: true },
      deny: [...OPENCLAW_DENIED_TOOLS],
    },
    security: { audit: { suppressions: [] } },
    agents: {
      defaults: {
        skipBootstrap: true,
        // `--profile <name>` isolates OPENCLAW_STATE_DIR and
        // OPENCLAW_CONFIG_PATH — it does NOT isolate the agent workspace.
        // Verified live 2026-08-08: a gateway booted under
        // `--profile empyralis-prov-live` created its workspace at
        // `~/.openclaw/workspace-empyralis-prov-live`, i.e. INSIDE the
        // operator's own shared `~/.openclaw` tree, as a sibling of every
        // other profile's workspace on that machine.
        //
        // That undercuts the one claim the whole adoption rests on — "one
        // isolated instance per customer, never shared", which is the reason
        // it is safe to run a single-operator-trust-model gateway inside a
        // multi-tenant product. Per-profile filenames are not isolation: the
        // directory's permissions, backups, and any sync client are shared.
        // Pinned explicitly into this instance's own state dir instead.
        workspace: `${plan.profileStateDir}/workspace`,
      },
    },
    channels,
    // NOTE: no Empyralis-owned marker key anywhere in this document. The
    // top-level schema is `additionalProperties: false` and `meta` is too, so
    // there is nowhere to stamp "Empyralis wrote this" that OpenClaw would
    // accept — `config patch` validates and would reject the whole write.
    // The provenance record lives on the Empyralis side instead
    // (./openclaw-provisioner.ts's state file, journaled and reported to the
    // cloud), which is the correct place for it anyway: a marker inside a
    // file we regenerate proves nothing about whether it is current.
  };

  const redactedConfig = JSON.parse(JSON.stringify(base)) as Record<string, unknown>;
  ((redactedConfig.gateway as Record<string, unknown>).auth as Record<string, unknown>).token = REDACTED;

  return {
    config: base,
    redactedConfig,
    fingerprint: fingerprintOpenClawConfig(redactedConfig),
    disabledChannels,
    widenings,
  };
}

/**
 * Pure copy-producing logic for OpenClawChannelsPanel.tsx, split out into its
 * own module (no React, no CSS import, no "use client") for the same reason
 * fleet-data.ts exists: openclaw-channel-copy.test.ts runs under plain
 * `npx tsx`, outside Next.js/webpack, and a module that imports a stylesheet
 * cannot be loaded that way. OpenClawChannelsPanel.tsx imports everything in
 * this file rather than redefining it.
 */

export type OpenClawCredentialField = {
  name: string;
  secret: boolean;
  type: string;
  file_alternative?: string | null;
  /** Generated. False for the fields a customer must supply to connect; true
   *  for mode-specific extras, network overrides and cosmetics. See
   *  `_split_primary_and_advanced` in the manifest generator — nothing here
   *  is a judgement made in the frontend. */
  advanced?: boolean;
};

/** The setup form has two halves, and this is the only place that decides
 *  which field goes where.
 *
 *  Setting up Telegram is: paste the bot token. Until 2026-08-15 the form
 *  rendered all SEVEN fields OpenClaw's schema declares for it — a webhook
 *  secret, an ack emoji, a custom API root, a proxy, a webhook host and a
 *  webhook URL beside the one thing anybody has. The split is DERIVED in the
 *  manifest generator, never listed here; this function only reads the flag,
 *  so a channel upstream adds tomorrow gets the same treatment with no
 *  frontend change.
 *
 *  A field with no `advanced` flag is PRIMARY. That is deliberate: a manifest
 *  generated before this axis existed renders exactly as it used to rather
 *  than collapsing every field out of sight. */
export function splitCredentialFields(fields: readonly OpenClawCredentialField[]): {
  primary: OpenClawCredentialField[];
  advanced: OpenClawCredentialField[];
} {
  return {
    primary: fields.filter((field) => !field.advanced),
    advanced: fields.filter((field) => Boolean(field.advanced)),
  };
}

export type OpenClawChannelCatalogEntry = {
  channel_key: string;
  channel_id: string;
  label: string;
  connect_method: "credential" | "pairing" | "plugin_absent";
  selection_label: string;
  docs_path: string | null;
  fields: OpenClawCredentialField[];
  requires_plugin: boolean;
  plugin_id: string | null;
};

export type OpenClawObservedField = { name: string; secret: boolean; type: string; set: boolean };

export type OpenClawObservedChannel = {
  channel_id: string;
  channel_key: string;
  installed: boolean;
  requires_plugin: boolean;
  enabled: boolean;
  configured: boolean;
  fields: OpenClawObservedField[];
  accounts: string[];
};

/** "A, B, and C" — the one place this pairing joins a channel-label list, so
 *  the wording stays in sync no matter how many labels the backend sends. */
export function formatChannelList(labels: string[]): string {
  if (labels.length === 0) return "";
  if (labels.length === 1) return labels[0];
  if (labels.length === 2) return `${labels[0]} and ${labels[1]}`;
  return `${labels.slice(0, -1).join(", ")}, and ${labels[labels.length - 1]}`;
}

/** The one thing this row needs the reader to do next, and why.
 *  Exactly one per row — a row with two calls to action has no call to action.
 *
 *  `detail` IS ALLOWED TO BE EMPTY, AND USUALLY SHOULD BE
 *  ------------------------------------------------------
 *  A state whose whole remedy is one button does not also need a sentence
 *  explaining the state to the customer. The panel already shows the three
 *  facts as chips; a sentence under them either repeats a chip or explains
 *  mechanism the customer never asked to know. The founder's standing
 *  instruction is that the customer is never told to install things — so the
 *  control does the work and says nothing. `detail` survives only where it
 *  carries something the chips and the button genuinely cannot: WHICH fields
 *  are still missing, and the two states with no browser action at all.
 *
 *  Modelled on CliSetupControl (hardware/[gatewayId]/page.tsx), which replaced
 *  this build's SSH copy-paste guidance with a button that just runs the
 *  install and verify-polls until the box's own state catches up. */
export type Remediation =
  | { kind: "install"; label: string; detail: string }
  | { kind: "credential"; label: string; detail: string }
  | { kind: "enable"; label: string; detail: string }
  | { kind: "elsewhere"; detail: string }
  | { kind: "unknown"; detail: string }
  | { kind: "needs_hardware"; detail: string }
  | { kind: "ready"; detail: string };

/** The ONE status word a channel CARD FACE shows.
 *
 *  A card face holds an icon, a label and exactly one pill — that is the whole
 *  card. The three independent facts (plugin installed / credential set /
 *  switched on) are NOT collapsed away: they are shown, all three, in the panel
 *  the card opens, beside the remediation sentence and the button that fixes
 *  it. What is collapsed is only what fits on a 4-across tile, and it collapses
 *  to the single most actionable state — the same thing `remediationFor`
 *  already picks, so the pill and the panel can never disagree.
 *
 *  Tones are the first-party pill vocabulary verbatim
 *  (.fleet-channel-card-pill--connected/--gateway/--locked/--setup), so a
 *  transported channel and a first-party one read as one grid rather than two
 *  colour systems side by side. "Ready", never "Connected": a connection is
 *  proven by a real message arriving and nothing on this screen has seen one.
 *  "needs_hardware" reuses the "gateway" tone AND THE EXACT LABEL
 *  `channelStatePill` (FleetAgentDetail.tsx) uses for the first-party
 *  no-gateway state — "Needs Gateway" — so the two halves of the grid read as
 *  one colour system in this state too, not just in the other four. */
export type ChannelCardPill = {
  label: string;
  tone: "connected" | "gateway" | "locked" | "setup";
};

export function channelCardPill(remediation: Remediation): ChannelCardPill {
  switch (remediation.kind) {
    case "ready":
      return { label: "Ready", tone: "connected" };
    case "install":
      // "Set up", not "Not installed": the first-party cards in the same grid
      // already say "Set up" for the same situation, and a plugin on a box is
      // mechanism the customer never asked about. Same word, same grid.
      return { label: "Set up", tone: "setup" };
    case "credential":
      return { label: "Needs credential", tone: "setup" };
    case "enable":
      return { label: "Switched off", tone: "setup" };
    case "elsewhere":
      return { label: "Link on the device", tone: "locked" };
    case "needs_hardware":
      return { label: "Needs Gateway", tone: "gateway" };
    default:
      return { label: "Unknown", tone: "locked" };
  }
}

export function remediationFor(
  entry: OpenClawChannelCatalogEntry,
  observed: OpenClawObservedChannel | undefined,
  reachable: boolean,
  // Three different facts collapsed into one boolean here on purpose:
  // callers with no gateway bound at all pass `hasGateway: false` and never
  // reach the `reachable`/`observed` branch below — "no computer paired" and
  // "a paired computer could not be reached" are different facts (the first
  // has no button that could ever do anything; the second is worth a retry)
  // and must not share the "unknown" copy. See the module-level Remediation
  // comment: this is the "needs_hardware" state that copy used to lie about.
  hasGateway: boolean,
): Remediation {
  if (!hasGateway) {
    return {
      kind: "needs_hardware",
      // Same fact, same words, as the first-party no-gateway hint
      // (FleetAgentDetail.tsx's LOCAL_BRIDGE_NO_GATEWAY_HINT) — a computer
      // that does not exist yet, not a computer that could not be reached.
      detail: "This agent has no computer of its own yet — set one up on the Hardware tab first, then point it at this channel.",
    };
  }
  if (!reachable || !observed) {
    return {
      kind: "unknown",
      detail: "This computer could not be reached, so its channel state is unknown.",
    };
  }
  if (entry.requires_plugin && !observed.installed) {
    return {
      kind: "install",
      // The control DOES the work; it does not describe it. "Install plugin"
      // above a sentence reading "The channel's plugin is not on this computer
      // yet." told the customer about a mechanism they never asked for and
      // then asked them to act on it. One button, in their terms.
      label: "Set up",
      detail: "",
    };
  }
  if (entry.connect_method === "plugin_absent") {
    return {
      kind: "unknown",
      // No mechanism: what the customer needs is that there is nothing for
      // them to fill in here yet, not which package is or isn't on a disk.
      detail: "Setup fields aren't known for this one yet.",
    };
  }
  if (entry.connect_method === "pairing") {
    return {
      kind: "elsewhere",
      // The honest version of a dead control. Their own selection label says
      // how it links; we do not invent a form that would submit nothing.
      detail: `${entry.selection_label} — there is no token to paste. Link it directly from this computer.`,
    };
  }
  // "Waiting on" names only the fields connecting actually requires. The
  // observed set is every field OpenClaw declares, so before the primary/
  // advanced split existed this sentence read, on a Telegram whose bot token
  // was already saved: "Waiting on webhookSecret, ackReaction, apiRoot, proxy,
  // webhookHost, webhookUrl." — six optional extras presented as six things
  // blocking the channel. A channel is not waiting on a proxy.
  //
  // Derived, never listed: `advanced` comes off the generated manifest, and a
  // field the catalog does not mention at all counts as required rather than
  // being silently dropped from the sentence.
  const advancedNames = new Set(
    entry.fields.filter((field) => field.advanced).map((field) => field.name),
  );
  const required = observed.fields.filter((field) => !advancedNames.has(field.name));
  const missing = required.filter((field) => !field.set);
  if (missing.length > 0) {
    return {
      kind: "credential",
      label: required.some((field) => field.set) ? "Finish credential" : "Add credential",
      detail: `Waiting on ${missing.map((field) => field.name).join(", ")}.`,
    };
  }
  if (!observed.enabled) {
    return {
      kind: "enable",
      label: "Turn on",
      // The chips above already say "credential set" and "off". A sentence
      // repeating them is not information, it is furniture.
      detail: "",
    };
  }
  return {
    kind: "ready",
    // "Ready", never "Connected". A connection is proven by a real message
    // arriving, and nothing on this screen has seen one. The three chips above
    // already carry the three facts, so this says the one thing they do not:
    // what the customer is still waiting for.
    detail: "Set up and switched on. A message arriving is what proves it.",
  };
}

/** The banner shown when this agent's box could not be asked for its own
 *  channel state — GET .../setup failed. Two genuinely different facts hide
 *  behind that one failure, and 2026-08-13's audit found them collapsed onto
 *  one sentence AND one retry button:
 *
 *    "the box is offline / stale / unhealthy" — worth retrying, so the
 *      toolbar's "Re-check this computer" button pairs with this banner.
 *    "the box answered fine, but the channel transport was never installed
 *      on it" (`gateway_capability_missing`) — the box is reachable, and no
 *      amount of re-checking installs the transport. A control that cannot
 *      work must not render (standing product law), so this banner pairs
 *      with NO retry control at all.
 *
 *  Driven off the STRUCTURED `observed_error_code` the backend now sends
 *  (gateway_reason_messages.KNOWN_REASON_TOKENS), never a text-match on the
 *  human `observed_error` message — CLAUDE.md's "stale string matching"
 *  rule. An unrecognized or missing code (an older response, or a genuinely
 *  unclassified failure) degrades to the original "could not be reached"
 *  copy, same as before this function existed. */
export type OpenClawObservedErrorBanner = { text: string; retryable: boolean };

export const OPENCLAW_UNREACHABLE_BANNER_TEXT =
  "This agent's computer could not be reached, so some channels below show an unknown state. Their setup fields are still accurate.";

export const OPENCLAW_CAPABILITY_MISSING_BANNER_TEXT = "Channels aren't set up on this computer yet.";

export function openclawObservedErrorBanner(
  observedError: string | null | undefined,
  observedErrorCode: string | null | undefined,
): OpenClawObservedErrorBanner | null {
  if (!observedError) return null;
  if (observedErrorCode === "gateway_capability_missing") {
    return { text: OPENCLAW_CAPABILITY_MISSING_BANNER_TEXT, retryable: false };
  }
  return { text: OPENCLAW_UNREACHABLE_BANNER_TEXT, retryable: true };
}


// ── Registry channel plugins ──────────────────────────────────────────────
//
// The channels OpenClaw's plugin registry publishes that its pinned build does
// not bundle. Everything above describes a RESOLVED channel — one with a known
// id, a config schema, and therefore a real setup form. These have none of
// that yet, and the difference is not a rendering detail:
//
//   a resolved channel   "here is the field, paste your token"
//   a registry offer     "this is not on this computer yet" — and its channel
//                        id does not exist until it is, because a third-party
//                        plugin registers its channel at runtime.
//
// So a registry card can only ever offer ONE action (install it), and it must
// never render a credential form. Once installed, the channel appears on the
// box and the next manifest regeneration carries it as a resolved channel with
// a real form.

export type OpenClawRegistryTrust = {
  is_official: boolean;
  release_channel: string | null;
  publisher: string | null;
  verification_tier: string | null;
  verification_scope: string | null;
  scan_status: string | null;
  has_provenance: boolean;
  trusted_openclaw_plugin: boolean;
  source_repo: string | null;
  installs: number;
  downloads: number;
};

export type OpenClawRegistryChannelPlugin = {
  plugin_id: string;
  npm_package: string;
  /** `clawhub:<name>@<version>` — their registry's own spec form, not npm's. */
  install_spec: string;
  version: string;
  label: string;
  summary: string | null;
  topics: string[];
  channel_id: null;
  channel_key: null;
  config_schema_present: false;
  connect_method: "plugin_absent";
  min_host_version: string | null;
  confirmed_by: string | null;
  trust: OpenClawRegistryTrust;
};

export type OpenClawRegistrySource = {
  /** The label a card face shows beside the name. Two words at most: this
   *  sits in a 4-across grid, not in a paragraph. */
  readonly label: string;
  readonly tone: "connected" | "setup" | "locked";
  /** The one-line answer to "who wrote this and does anyone vouch for it",
   *  shown in the panel the card opens — never on the face. */
  readonly detail: string;
};

/**
 * How much the registry itself vouches for a plugin, in the customer's words.
 *
 * THIS IS NEVER A FILTER. Every channel OpenClaw carries is offered, community
 * ones included — that is the whole point of adopting their transport. But a
 * plugin published by OpenClaw and one published by a stranger with nine
 * installs are not the same risk, and a channel plugin runs third-party code
 * beside the owner's messages while contributing its own tool surface that the
 * global `tools.*` lockdown does not reach. So the difference is STATED, on
 * the same card, in plain language — and the install button is identical on
 * both, because presenting a fact is not the same as putting a warning in
 * someone's way.
 *
 * Derived from the registry's own fields in priority order, never from a list
 * of package names: `isOfficial` (OpenClaw published it), then their scanner's
 * verdict, then the verification tier. A plugin added upstream tomorrow is
 * classified by the same rules with no code change here.
 */
export function registrySourceFor(plugin: OpenClawRegistryChannelPlugin): OpenClawRegistrySource {
  const trust = plugin.trust;
  const publisher = trust.publisher ? `@${trust.publisher}` : "an independent publisher";
  const reach =
    trust.installs > 0
      ? `${trust.installs.toLocaleString()} ${trust.installs === 1 ? "install" : "installs"}`
      : "no installs yet";
  if (trust.is_official || trust.trusted_openclaw_plugin) {
    return {
      label: "Official",
      tone: "connected",
      detail: `Published by OpenClaw · ${reach}.`,
    };
  }
  // Their own scanner flagged the artifact. Stated plainly and WITHOUT
  // blocking the install — it is their signal, not our verdict, and the owner
  // is the one who gets to weigh it.
  if (trust.scan_status && trust.scan_status.toLowerCase() !== "clean") {
    return {
      label: "Community",
      tone: "setup",
      detail:
        `Published by ${publisher} · ${reach}. OpenClaw's scanner flagged this ` +
        `package as ${trust.scan_status.toLowerCase()}.`,
    };
  }
  if (trust.verification_tier === "source-linked") {
    return {
      label: "Community",
      tone: "locked",
      detail: `Published by ${publisher} · ${reach} · source linked${
        trust.source_repo ? ` (${trust.source_repo})` : ""
      }.`,
    };
  }
  return {
    label: "Community",
    tone: "locked",
    detail: `Published by ${publisher} · ${reach}.`,
  };
}

/**
 * A registry card's face pill.
 *
 * Only three states are expressible, and none of them is a credential state —
 * a plugin that is not installed has no schema, so there is nothing to be
 * missing. Once it IS installed the channel stops being a registry offer
 * entirely and is rendered by the resolved-channel path above.
 */
export function registryChannelPill(params: {
  hasGateway: boolean;
  installed: boolean;
}): { label: string; tone: "connected" | "setup" | "locked" | "gateway" } {
  if (!params.hasGateway) return { label: "Needs Gateway", tone: "gateway" };
  if (params.installed) return { label: "Ready", tone: "connected" };
  return { label: "Set up", tone: "setup" };
}

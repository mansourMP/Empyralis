/**
 * Pure copy-producing logic for OpenClawChannelsPanel.tsx, split out into its
 * own module (no React, no CSS import, no "use client") for the same reason
 * fleet-data.ts exists: openclaw-channel-copy.test.ts runs under plain
 * `npx tsx`, outside Next.js/webpack, and a module that imports a stylesheet
 * cannot be loaded that way. OpenClawChannelsPanel.tsx imports everything in
 * this file rather than redefining it.
 */

import type { ChannelSetupWizard } from "./channel-setup-flow";

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
  /** HOW a person obtains what `fields` asks them to type, in the transport's
   *  own words — generated in the same manifest pass as the fields themselves.
   *  Optional because a backend predating it must still render the bare form
   *  it always did; see channel-setup-flow.ts. */
  setup_wizard?: ChannelSetupWizard | null;
};

export type OpenClawObservedField = {
  name: string;
  secret: boolean;
  type: string;
  set: boolean;
  /** Carried by the box since it started deriving a live shape. Absent from an
   *  older gateway's reply, and absent means PRIMARY — the same default
   *  `splitCredentialFields` already takes for a manifest that predates the
   *  axis. */
  advanced?: boolean;
  file_alternative?: string | null;
};

/** How a channel that takes no pasted credential is LINKED — read off the box,
 *  not off a list here.
 *
 *  The generated manifest can say a channel has nothing to paste
 *  (`connect_method: "pairing"`, seven channels today) but not how it is linked
 *  instead, because that is a property of the plugin installed on THIS
 *  computer. OpenClaw answers it per channel:
 *
 *      openclaw channels capabilities --channel all --json
 *        -> channels[].plugin.gatewayMethodDescriptors[].name
 *
 *  A plugin declaring `web.login.start` AND `web.login.wait` owns OpenClaw's
 *  QR-login seam — the same test their own `resolveWebLoginProvider` makes.
 *  Measured on openclaw@2026.6.10: WhatsApp declares both, Telegram declares
 *  none. Nothing here names either channel, so the day a plugin grows that seam
 *  its flow appears on its own.
 *
 *  `null` (the field absent) is a THIRD value and not a synonym for "no QR": it
 *  means the box did not report on this channel at all, which normally means
 *  the channel is not switched on yet. */
export type OpenClawChannelLink = {
  gateway_methods: string[];
  supports_qr_login: boolean;
};

export type OpenClawObservedChannel = {
  channel_id: string;
  channel_key: string;
  installed: boolean;
  requires_plugin: boolean;
  enabled: boolean;
  configured: boolean;
  fields: OpenClawObservedField[];
  accounts: string[];
  link?: OpenClawChannelLink | null;
  /** How the BOX says this channel connects. Normally the same answer the
   *  catalog gives; different only for a channel the manifest calls
   *  `plugin_absent`, whose plugin is now installed on this computer and has
   *  contributed its own `channels.<id>` schema node. Optional because a
   *  gateway predating that derivation does not send it. */
  connect_method?: string;
};

/**
 * The shape actually in force for a channel: the catalog's, unless the catalog
 * admits it does not know and the box does.
 *
 * The generated manifest is a property of the PINNED OpenClaw version, and
 * four channels contribute their config node only once their plugin is
 * installed — so the manifest carries `connect_method: "plugin_absent"` and no
 * fields for them. That is right on a fresh box and wrong the moment the
 * plugin lands, which is exactly what a successful "Set up" does. Before this
 * function existed, all four dead-ended after a successful install: pill
 * "Unknown", detail "Setup fields aren't known for this one yet", forever, on
 * a computer that knew.
 *
 *     catalog   the PRE-INSTALL BELIEF   ─┐
 *     box       the OBSERVATION          ─┴─▶ the observation wins,
 *                                             and ONLY where the belief is
 *                                             `plugin_absent`
 *
 * Narrow on purpose: a channel the pinned build declares a node for is
 * answered by the catalog exactly as before, so this cannot re-shape the
 * twenty-three channels that already work. And a box that answers
 * `plugin_absent` too — plugin installed, still contributing nothing — keeps
 * today's honest unknown rather than being smoothed into a guess.
 */
export function effectiveChannelShape(
  entry: OpenClawChannelCatalogEntry,
  observed: OpenClawObservedChannel | undefined,
): { connect_method: "credential" | "pairing" | "plugin_absent"; fields: OpenClawCredentialField[] } {
  if (entry.connect_method !== "plugin_absent") {
    return { connect_method: entry.connect_method, fields: entry.fields };
  }
  const method = observed?.connect_method;
  if (method !== "credential" && method !== "pairing") {
    return { connect_method: "plugin_absent", fields: [] };
  }
  return {
    connect_method: method,
    fields: (observed?.fields ?? []).map((field) => ({
      name: field.name,
      secret: field.secret,
      type: field.type,
      file_alternative: field.file_alternative ?? null,
      advanced: Boolean(field.advanced),
    })),
  };
}

/** STEP 2 OF THE SETUP FLOW: how this channel is connected.
 *
 *  Step 1 (which variant of the platform — full account vs bot) is
 *  channel-doors.ts's job and is unchanged: one real door opens straight into
 *  setup, two or more show a picker with each door's consequence on its face.
 *  This is the step AFTER that pick, and like the doors it is DERIVED — from
 *  the manifest's credential shape and the box's own link metadata, never from
 *  a per-channel table.
 *
 *      paste     the channel declares credential fields    -> a form
 *      scan      its plugin declares OpenClaw's QR seam    -> a code to scan
 *      device    neither: linked physically on the computer -> say so, no form
 *      unknown   the plugin is not there, so its fields are genuinely unknown
 *
 *  There is deliberately no "phone number + login code" member yet: no channel
 *  in the pinned build's manifest declares that shape, and inventing a member
 *  nothing produces is how a UI grows a branch that has never once rendered.
 *  See the report for `telegram-userbot`, which would be its first producer. */
/** `unknown_link`: a pairing channel whose box has not reported its link
 *  shape yet, because OpenClaw only exposes that once the channel is enabled.
 *  Distinct from `unknown` (no plugin at all) and from `device` (the box
 *  answered, and this one genuinely has no QR flow). Starting the link is
 *  what resolves it — see connectMethodFor. */
export type ChannelConnectMethod = "paste" | "scan" | "device" | "unknown" | "unknown_link";

export function connectMethodFor(
  entry: OpenClawChannelCatalogEntry,
  observed: OpenClawObservedChannel | undefined,
): ChannelConnectMethod {
  // The box's answer where the catalog has none — see effectiveChannelShape.
  const shape = effectiveChannelShape(entry, observed);
  if (shape.connect_method === "plugin_absent") return "unknown";
  if (shape.connect_method === "credential") return "paste";
  // `pairing` splits on what the BOX says, because that is the only place the
  // answer exists — but "the box has not said yet" is a THIRD state, and
  // collapsing it into "device" produced a dead end nobody could escape.
  //
  // OpenClaw only registers a channel plugin's provider once the channel is
  // ENABLED (openclaw-channel-link.ts documents this, confirmed the hard way:
  // with channels.whatsapp.enabled false, web.login.start answers "web login
  // provider is not available"). So `observed.link` is null until enabled —
  // and since 2026-08-15 we deliberately only enable channels the owner has
  // set up. Linking IS how an owner sets a pairing channel up, so:
  //
  //   link null  -> not enabled yet -> the panel showed "there is no token to
  //                 paste, link it directly from this computer", a dead end
  //   to escape it you must start the link
  //   to start the link the button must render
  //   the button only rendered when link was non-null
  //
  // A circular gate, and it is why WhatsApp could never be linked from the UI.
  // `link_start` calls ensureChannelEnabled first, so pressing the control is
  // exactly what resolves the unknown. Offer it rather than guessing which
  // pairing channels can QR — a guess would be a hand-maintained channel list
  // in disguise, and this file exists to avoid those.
  // An UNREACHABLE box is not the same as an unenabled channel, and this
  // distinction is load-bearing: with no `observed` at all we know nothing
  // and must not offer a control that cannot work — degrade to the honest
  // device sentence, exactly as before. `observed` present with a null
  // `link` is the box answering "this channel is not enabled", which is the
  // state pressing the control resolves.
  if (!observed) return "device";
  if (observed.link == null) return "unknown_link";
  return observed.link.supports_qr_login ? "scan" : "device";
}

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
  /** A channel with nothing to paste whose plugin DOES own OpenClaw's QR seam:
   *  a real control, because there is now a real flow behind it. Distinct from
   *  `elsewhere`, which is the honest no-control state for a channel that
   *  genuinely can only be linked by hand on the computer. */
  /** `qrKnown`: the box has CONFIRMED this channel owns OpenClaw's QR seam.
   *  False means we are offering the control to find out — the channel is not
   *  enabled yet, so its link shape is genuinely unknown. The card face must
   *  not promise a QR in that case: iMessage links off a local database and
   *  Signal off a device pairing, and telling their card "Scan a code" sends
   *  the customer looking for a square that will never appear. */
  | { kind: "link"; label: string; detail: string; qrKnown: boolean }
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
    case "link":
      // "Scan a code" and not "Link on the device": on this card the linking
      // happens HERE, and the face has to say which of the two it is or the
      // pill is telling the customer to go somewhere they do not need to go.
      //
      // But ONLY when the box has confirmed the QR seam. Until a pairing
      // channel is enabled OpenClaw does not report its link shape, and
      // promising "Scan a code" there sent iMessage and Signal customers
      // looking for a square that does not exist — iMessage links off a
      // local database, Signal off a device pairing. Unknown says "Set up",
      // the same neutral word the install state already uses.
      return remediation.qrKnown
        ? { label: "Scan a code", tone: "setup" }
        : { label: "Set up", tone: "setup" };
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
  // The STRUCTURED reason the read failed, when it did — the same code
  // openclawObservedErrorBanner already branches on, never a text match
  // (CLAUDE.md: "match on stable codes, never on prose"). MAN-329: before
  // this parameter existed, every unreachable-read produced the SAME
  // sentence ("This computer could not be reached...") no matter WHY the
  // read failed — even though the banner above the grid already knew to
  // tell "gateway_capability_missing" (the box answered fine; this build's
  // gateway has never had the channel transport installed, or predates the
  // capability entirely — "reconnect" cannot fix that) apart from an
  // actually offline/stale/unhealthy box. That left every card on the grid,
  // and the one-sentence detail inside each card's own panel, telling the
  // customer their computer was unreachable while the banner directly above
  // it, for the identical failure, correctly said the computer was fine.
  // Optional so every pre-existing caller (this file's own test matrix
  // included) keeps behaving exactly as before when it has no code to pass.
  observedErrorCode?: string | null,
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
    if (observedErrorCode === "gateway_capability_missing") {
      // The box answered fine — this is not a reachability problem. Reuses
      // the banner's own constant rather than a second hand-written
      // sentence, so the grid and the panel it opens can never disagree
      // about the same failure.
      return { kind: "unknown", detail: OPENCLAW_CAPABILITY_MISSING_BANNER_TEXT };
    }
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
  // The catalog is the pre-install belief and the box is the observation. A
  // channel whose plugin the manifest never saw is describable the moment that
  // plugin is on THIS computer, and reading the catalog's `plugin_absent`
  // here is what dead-ended all four of them after a successful install.
  const shape = effectiveChannelShape(entry, observed);
  if (shape.connect_method === "plugin_absent") {
    return {
      kind: "unknown",
      // No mechanism: what the customer needs is that there is nothing for
      // them to fill in here yet, not which package is or isn't on a disk.
      detail: "Setup fields aren't known for this one yet.",
    };
  }
  if (shape.connect_method === "pairing") {
    // Two different situations used to share one dead-end sentence. They are
    // not the same: one of them now has a real flow behind it.
    const method = connectMethodFor(entry, observed);
    if (method === "scan") {
      return {
        kind: "link",
        // The control does the work and says nothing about mechanism — same
        // rule as "Set up" above. Where the code has to be scanned is the
        // channel's own instruction and arrives with the code itself.
        label: observed?.accounts.length ? "Link again" : "Show code",
        detail: "",
        qrKnown: true,
      };
    }
    if (method === "unknown_link") {
      // The box has not been asked yet, because this channel is not enabled
      // and OpenClaw does not expose a link shape until it is. Pressing this
      // enables it and asks — which is the only way to find out, and is also
      // exactly what the owner wants to happen. One control, no sentence
      // about mechanism, and no guess about which pairing channels can QR.
      return { kind: "link", label: "Set up", detail: "", qrKnown: false };
    }
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
    shape.fields.filter((field) => field.advanced).map((field) => field.name),
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

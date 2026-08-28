/**
 * The channel "doors" model — the ways a single channel can be connected, and
 * the rule that decides whether the customer is asked to choose between them.
 *
 * Split out of FleetAgentDetail.tsx (no React, no CSS import, no "use client")
 * for the same reason openclaw-channel-copy.ts is: channel-doors.test.ts runs
 * under plain `npx tsx`, outside Next.js/webpack, and a module that imports a
 * stylesheet cannot be loaded that way. Before this split, every door string
 * was pinned into openclaw-channel-copy.test.ts BY HAND as a copy-pasted
 * literal — a check whose expected set was transcribed from the thing it
 * checks, which goes stale silently the moment someone edits the component
 * (CLAUDE.md: "a check that derives its own expectations from the thing it
 * checks is blind, and reports 'passed'"). The test now imports the real data.
 *
 * THE DOOR-COUNT RULE — DERIVED, NEVER AUTHORED PER CHANNEL
 * ---------------------------------------------------------
 * Whether a channel shows a picker is a function of how many real doors it
 * has, and of nothing else:
 *
 *     real doors == 1   ─▶  "direct":  the card opens straight into that
 *                                      door's setup. An intermediate screen
 *                                      offering one option is a dead click,
 *                                      and a dead control is a product-law
 *                                      violation (CLAUDE.md).
 *     real doors >= 2   ─▶  "picker":  the card opens the door choice FIRST,
 *                                      because the choice has consequences
 *                                      that differ (see `consequence`).
 *     real doors == 0   ─▶  "none":    nothing to open.
 *
 * Hardcoding "Telegram gets a picker" would rot on contact: as transported
 * paths land, Telegram gains a third door and WhatsApp a second. The count
 * drives the behaviour so that adding a door to this table is the whole
 * change.
 */

/** The consequence of walking through a door, stated ON THE DOOR — before it
 *  is chosen, never in a warning after a QR code has been scanned.
 *
 *  This is the entire reason a picker earns its place. Telegram's two doors
 *  are not two setup procedures, they are two RISK profiles: one runs a bot
 *  that is a separate identity, the other signs in as the owner and puts the
 *  owner's own number in reach of a platform ban. That has already happened
 *  to a real person here. One clear line, stated plainly — a professional
 *  tool labels, it does not lecture (CLAUDE.md craft doctrine), so this is a
 *  fact about what happens, never a paragraph of warnings.
 *
 *  `risk` is only ever set where the consequence is real and specific.
 *  Never invent one to make a door look symmetrical. */
export type ChannelDoorConsequence = {
  tone: "safe" | "risk";
  text: string;
};

export type ChannelDoor = {
  key: string;
  label: string;
  /** What this door IS, in one line. Setup instructions belong in the setup
   *  form the door opens, not here. */
  body: string;
  /** False = not built on this deployment. Such a door is not rendered at all
   *  and does not count toward the picker threshold — a door that cannot be
   *  walked through is not a way to connect. */
  real: boolean;
  /** This door binds to THIS agent's own gateway (a paired computer / VPS): a
   *  "full account" login runs a real client process on that box, which a
   *  cloud-only agent has nowhere to run. Surfaced on the door face BEFORE the
   *  pick, and an unmet requirement makes the door unavailable rather than a
   *  pick that fails once opened. */
  requiresHardware?: boolean;
  consequence?: ChannelDoorConsequence;
};

/** The first-party half of the channel grid: the platform Empyralis implements
 *  itself, and the backend connection id that carries its live status.
 *
 *  LIVES HERE, NOT IN ChannelsTab, so the copy test can import it.
 *  ---------------------------------------------------------------
 *  These seven labels are rendered into the DOM verbatim, so they belong to the
 *  same family of strings openclaw-channel-copy.test.ts already checks. While
 *  they sat inside FleetAgentDetail.tsx — a React component that imports a
 *  stylesheet, which the `tsx` test runner cannot load — the test could only
 *  carry them as hand-copied literals, i.e. a check transcribing its
 *  expectations from the thing it checks. Every id here is also a key of
 *  CHANNEL_DOORS below, which is now assertable rather than merely true. */
// 2026-08-14 full OpenClaw channel cutover: whatsapp_personal, signal_personal
// and imessage_personal are DELETED from this grid entirely — their
// first-party gateway runtimes are gone, and the live replacements
// (openclaw_whatsapp, openclaw_signal, openclaw_imessage) now appear
// automatically in the DERIVED OpenClaw grid below (groupTransportedChannels),
// exactly like every other transported platform. Telegram stays, but see its
// CHANNEL_DOORS entry below — its "full_account" door is deleted too, so it
// is now single-door ("direct" mode) rather than a picker.
export const CHANNEL_GRID_PLATFORMS: { label: string; id: string }[] = [
  { label: "Telegram", id: "sage_telegram_hosted" },
  { label: "Slack", id: "slack" },
  { label: "Discord", id: "discord_bot" },
  { label: "WeChat / WeCom", id: "wechat_official" },
];

/** Each channel shows only the connection MODES that are real, safe, and built
 *  today for that platform — never a door that fails, and never a second mode
 *  standing in for one that doesn't exist yet. "Full account" doors bind to
 *  THIS agent's own preferred_gateway_id (see ChannelsTab's `agentGatewayId`),
 *  never to a workspace-wide/Sage-routed session. */
export const CHANNEL_DOORS: Record<string, ChannelDoor[]> = {
  // 2026-08-14 full OpenClaw channel cutover: Telegram's "full_account" door
  // (real MTProto/gramjs session, phone+code+2FA, the same account-ban risk
  // the door's own consequence text warned about) is DELETED along with the
  // first-party gramjs runtime it opened. OpenClaw's pinned build has no
  // personal-account credential shape for Telegram at all — bot-only is now
  // the ONLY way to connect Telegram, not a choice, so a single real door
  // means planDoors() resolves this card to "direct" mode automatically:
  // the card opens straight into Chatbot setup, no picker, nothing to
  // choose between. That absence IS the honest statement that Telegram runs
  // as a bot here — see this door's own body text.
  // 2026-08-28: the hosted door is BACK, and Telegram is a picker again — but
  // for a completely different reason than the deleted `full_account` door.
  // The platform's own shared Telegram bot ("no BotFather, no token") has been
  // fully built in the backend the whole time and reachable from NOTHING since
  // TelegramPairPanel.tsx was deleted: the only frontend call to any hosted
  // endpoint was SystemHealthButton's liveness probe. So the only way to
  // connect Telegram was to leave the product for BotFather and come back with
  // a token — on the founder's own #1 launch channel, and on the surface
  // CLAUDE.md calls the critical path.
  //
  // THE TWO DOORS DIFFER IN WHO ANSWERS, and that is what the picker is for:
  //   byo_bot     your own bot token ─▶ route_agent_inbound  ─▶ THIS agent
  //   hosted_bot  the shared bot     ─▶ dispatch_sage_reply  ─▶ SAGE
  // The hosted lane is workspace-scoped and passes no `specialist_context`,
  // so it answers as the workspace assistant. hosted_bot's body says so in
  // plain words — see telegram-hosted-pairing.ts's header for the file:line
  // trace and for why making it agent-aware is unfinished, not impossible.
  sage_telegram_hosted: [
    {
      key: "hosted_bot",
      label: "Empyralis bot",
      body: "Message the bot Empyralis already runs — nothing to create, nothing to paste. It answers as your workspace assistant, which can reach your projects, tasks and documents, rather than as this agent.",
      real: true,
      consequence: {
        tone: "safe",
        text: "Connects in one tap. Shared across your workspace, so it replies as your assistant, not as this agent.",
      },
    },
    {
      key: "byo_bot",
      label: "Chatbot",
      body: "This agent runs as a Telegram bot of its own — people message a bot you create in BotFather, never your own Telegram account. Telegram's platform has no way to connect a real personal account to an automated agent, so a bot is the only way to reach this agent directly.",
      real: true,
      consequence: {
        tone: "safe",
        text: "Needs a token from BotFather. This agent answers in its own voice, and your own account can't be banned.",
      },
    },
  ],
  slack: [
    {
      key: "oauth",
      label: "App",
      body: "Connect a Slack workspace — signed mentions and DMs route to your AI.",
      real: true,
    },
  ],
  discord_bot: [
    {
      key: "byo_bot",
      label: "Bot",
      body: "Give this agent its own Discord bot — paste the token from Discord's developer portal.",
      real: true,
    },
  ],
  // whatsapp_personal, signal_personal and imessage_personal DELETED
  // 2026-08-14 (full OpenClaw channel cutover) — their first-party doors
  // (Baileys QR pairing, the signal-cli/BlueBubbles local bridges) are gone.
  // The live replacements are openclaw_whatsapp/openclaw_signal/
  // openclaw_imessage, which appear in the DERIVED OpenClaw grid below with
  // their own "pairing" door via transportedDoorBody — the identical QR/
  // bridge-linked shape these first-party doors used to describe, now
  // generated from OpenClaw's own manifest rather than authored by hand here.
  wechat_official: [
    {
      key: "app_credential_pair",
      label: "Official Account / WeCom",
      body: "This agent's own WeChat Official Account or WeChat Work (WeCom) bot — paste the AppID/AppSecret (or CorpID/CorpSecret/AgentId) from your own admin console.",
      real: true,
    },
  ],
};

/** What the panel a channel card opens should do, decided by door COUNT.
 *  `doors` is the real-door list in both non-empty modes, so a caller never
 *  re-filters and can never disagree with the mode. */
export type ChannelDoorPlan =
  | { mode: "none"; doors: ChannelDoor[] }
  | { mode: "direct"; door: ChannelDoor; doors: ChannelDoor[] }
  | { mode: "picker"; doors: ChannelDoor[] };

/** The whole behavioural rule, in one place, driven by the count.
 *
 *  Takes the doors themselves rather than a channel id, so a channel whose
 *  doors are DERIVED (the transported platforms below) goes through the exact
 *  same rule as one whose doors are authored — one rule, not two that agree
 *  today. */
export function planDoors(candidates: ChannelDoor[]): ChannelDoorPlan {
  const doors = candidates.filter((door) => door.real);
  if (doors.length === 0) return { mode: "none", doors };
  if (doors.length === 1) return { mode: "direct", door: doors[0], doors };
  return { mode: "picker", doors };
}

export function planChannelDoors(channelId: string | null | undefined): ChannelDoorPlan {
  return planDoors(channelId ? CHANNEL_DOORS[channelId] || [] : []);
}

/** The one secondary signal a CARD FACE carries: that opening this card asks a
 *  question rather than starting a setup.
 *
 *  Since one platform became one card, a card can hide two or three genuinely
 *  different ways in (Zalo has three, Telegram two) and nothing on the grid said
 *  so — the picker arrived as a surprise. This is the smallest honest fix: the
 *  COUNT, from the same `planDoors` that decides whether a picker is shown at
 *  all, so the face and the panel cannot disagree and nothing here names a
 *  channel. A one-door card has nothing to say, and says nothing — a caption
 *  reading "1 way to connect" on nineteen of twenty-four cards is furniture.
 *
 *  Never a second pill: the card face is icon + label + one status pill, and
 *  this rides under it as a smaller, dimmer line (.fleet-channel-card-ways). */
export function channelDoorChoiceNote(plan: ChannelDoorPlan): string | null {
  return plan.mode === "picker" ? `${plan.doors.length} ways to connect` : null;
}

/** Whether this agent can walk through this door RIGHT NOW.
 *
 *  `doorConnected` is deliberately an input rather than something computed
 *  here: an already-connected full-account door stays available even with no
 *  gateway currently attached, because the customer must still be able to open
 *  it to see (and disconnect) the session that exists. */
export type ChannelDoorHardwareState = "not-required" | "ready" | "missing";

export function channelDoorHardwareState(
  door: ChannelDoor,
  { hasHardware, doorConnected }: { hasHardware: boolean; doorConnected: boolean },
): ChannelDoorHardwareState {
  if (!door.requiresHardware) return "not-required";
  if (hasHardware || doorConnected) return "ready";
  return "missing";
}

/** The SECOND axis a door can be unavailable on, and it is not about this
 *  agent at all: some doors depend on something the DEPLOYMENT provides.
 *  Telegram's hosted door needs the platform's own shared bot token to be set
 *  (sage_telegram_hosted_service.is_configured()); a deployment without one
 *  can never complete that pairing, so the door must say so rather than render
 *  a Connect button whose first action returns 503.
 *
 *  THREE VALUES, NOT TWO. `null` means "we have not found out yet", and it
 *  resolves to AVAILABLE: hiding a door because a status call has not landed
 *  is the same "empty vs. could not load" collapse the outcome-honesty law
 *  forbids, and it would flicker the door out of existence on every load.
 *  `undefined` (every pre-existing caller) means the door has no deployment
 *  dependency, so behaviour is byte-for-byte unchanged for them. */
export type ChannelDoorAvailabilityInput = {
  hasHardware: boolean;
  doorConnected: boolean;
  deploymentSupported?: boolean | null;
};

export function isChannelDoorAvailable(
  door: ChannelDoor,
  state: ChannelDoorAvailabilityInput,
): boolean {
  if (state.deploymentSupported === false) return false;
  return channelDoorHardwareState(door, state) !== "missing";
}

/** The hardware fact, on the door face, before the pick. `not-required` has
 *  nothing to say — a note that says "no computer needed" is noise on six of
 *  the seven channels. */
export function channelDoorHardwareNote(state: ChannelDoorHardwareState): string | null {
  switch (state) {
    case "ready":
      return "Needs a computer — this agent has one";
    case "missing":
      return "Needs a computer — connect one on the Hardware tab";
    default:
      return null;
  }
}

/** What a door the agent cannot currently complete says INSTEAD of a setup
 *  form. Not a caption under a live-looking control: the control is not
 *  rendered at all. */
export function channelDoorUnavailableReason(
  door: ChannelDoor,
  state?: { deploymentSupported?: boolean | null },
): string {
  // The deployment axis is checked FIRST because it outranks hardware: a door
  // this installation cannot offer at all is not fixed by pairing a computer,
  // and sending someone to the Hardware tab for it would be a false next step.
  if (state?.deploymentSupported === false) {
    return "This one isn't set up here — the Empyralis-run bot isn't configured on this installation. Use the other way in, or ask whoever runs this deployment.";
  }
  return door.requiresHardware
    ? "This one runs on the agent's own computer, and this agent doesn't have one yet. Connect a computer on the Hardware tab, then come back."
    : "This one can't be set up from here yet.";
}

// ── ONE PLATFORM = ONE CARD. VARIANTS ARE ALWAYS DOORS. ─────────────────────
//
// Telegram has been one card with two doors since the picker landed. The
// transported channels arrived modelled the opposite way, because the
// transport models every variant of a platform as its own channel:
//
//     BEFORE                                AFTER
//       ▢ Zalo            (Bot API)           ▢ Zalo ──opens──▶ ┌ Bot API ┐
//       ▢ Zalo Personal   (QR on the box)                       │ Personal│
//       ▢ Zalo ClawBot    (QR)                                  └ ClawBot ┘
//       three cards, one platform             one card, three doors,
//       — the same concept rendered           the same door-COUNT rule
//       two opposite ways in one grid         Telegram already uses
//
// A hand-written list of "these ids are really one platform" is the exact
// mistake this codebase has already made and corrected twice on this surface
// (the five-channel tuple, the parallel label map). So the grouping is
// DERIVED, from two INDEPENDENT axes that must BOTH agree:
//
//   1. ID FAMILY     one channel's id is a proper prefix of the other's
//                    (a variant id extends its platform's id: zalo ⊂ zalouser,
//                    zalo ⊂ zaloclawbot). Comes from the transport's registry.
//   2. LABEL FAMILY  both labels open with the same word ("Zalo", "Zalo
//                    Personal", "Zalo ClawBot"). Comes from their catalog /
//                    package display names — a different upstream field.
//
// Requiring both is what makes the dangerous direction safe. WeCom (WeChat
// Work) and Weixin (consumer WeChat) are DIFFERENT PRODUCTS — CLAUDE.md says
// so explicitly — and they fail BOTH axes ("wecom" is no prefix of "weixin";
// the leading label words differ). A future "Google Chat"/"Google Meet" pair
// shares a label word and no id prefix, so it stays two cards. The failure
// mode of a miss is a variant that keeps its own card, i.e. exactly today's
// behaviour; the failure mode of a false merge would be two products sharing
// one card, and that needs two independent upstream fields to conspire.

/** The structural shape this derivation needs from a transported channel —
 *  a subset of OpenClawChannelCatalogEntry, declared locally so this module
 *  stays free of the copy module (and of anything that imports React). */
export type TransportedChannelInput = {
  channel_key: string;
  channel_id: string;
  label: string;
  selection_label: string;
  connect_method: "credential" | "pairing" | "plugin_absent";
};

export type TransportedChannelPlatform = {
  /** The base variant's channel_key. Identity of the CARD. */
  key: string;
  /** The platform's own name — the base variant's label ("Zalo"), never a
   *  variant's ("Zalo Personal"). */
  label: string;
  /** The same key a first-party card looks its icon up by. All variants of a
   *  platform share one mark, so the base's is the platform's. */
  iconKey: string;
  variants: TransportedChannelInput[];
  /** One door per variant, in the same order. `door.key` is the variant's
   *  channel_key, so the panel resolves a pick straight back to a live row. */
  doors: ChannelDoor[];
};

/** The transport namespaces some of its own channel ids with its own name
 *  (`openclaw-zaloclawbot`). That prefix is not part of the platform, and it
 *  is recoverable structurally: `channel_key` is the prefix plus the id, so
 *  the prefix names the transport without this module naming it.
 *
 *  Exported as `transportedPlatformToken` because channel-platform.ts needs
 *  the identical token to decide whether a FIRST-PARTY card names the same
 *  platform as a transported one — two derivations of "which platform is
 *  this" would be two opinions that agree only today. */
export function transportedPlatformToken(entry: TransportedChannelInput): string {
  return platformToken(entry);
}

function platformToken(entry: TransportedChannelInput): string {
  const key = String(entry.channel_key || "").toLowerCase();
  const id = String(entry.channel_id || "").toLowerCase();
  const vendor = key.endsWith(id) ? key.slice(0, key.length - id.length).replace(/[^a-z0-9]+$/, "") : "";
  const stripped = vendor && id.startsWith(`${vendor}-`) ? id.slice(vendor.length + 1) : id;
  return stripped.replace(/[^a-z0-9]/g, "");
}

/** The first word of the display label, normalised. "Zalo Personal" -> "zalo",
 *  "WeCom（企业微信）" -> "wecom（企业微信）"'s leading run -> "wecom". */
function labelToken(entry: TransportedChannelInput): string {
  return String(entry.label || "")
    .trim()
    .split(/[\s/]+/)[0]
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "");
}

function sameChannelPlatform(a: TransportedChannelInput, b: TransportedChannelInput): boolean {
  const [ta, tb] = [platformToken(a), platformToken(b)];
  if (!ta || !tb || ta === tb) return false;
  const idFamily = tb.startsWith(ta) || ta.startsWith(tb);
  if (!idFamily) return false;
  const [la, lb] = [labelToken(a), labelToken(b)];
  return Boolean(la) && la === lb;
}

/** The variant's own name for its door face. Their label minus the platform's
 *  ("Zalo Personal" -> "Personal"), falling back to the parenthetical in their
 *  selection label ("Zalo (Bot API)" -> "Bot API") for the base variant, whose
 *  label IS the platform name and therefore leaves nothing behind. Their words
 *  throughout — the same rule the panel already follows by rendering
 *  `selection_label` verbatim. */
export function transportedDoorLabel(entry: TransportedChannelInput, platformLabel: string): string {
  const label = String(entry.label || "").trim();
  const remainder =
    label.toLowerCase().startsWith(platformLabel.toLowerCase()) && label.length > platformLabel.length
      ? label.slice(platformLabel.length).trim()
      : label === platformLabel
        ? ""
        : label;
  if (remainder) return remainder;
  const parenthetical = /\(([^)]+)\)/.exec(String(entry.selection_label || ""));
  return parenthetical ? parenthetical[1].trim() : label;
}

/** What a door IS, in one line — and, since these doors are derived, the place
 *  the difference between them is stated before the pick. Their own selection
 *  label plus the one fact that actually differs between two ways into the
 *  same platform: whether it is set up from here, or linked physically on the
 *  agent's computer.
 *
 *  Deliberately NOT a `consequence`: that field carries a real, specific risk
 *  ("Telegram can ban your number"), and the manifest cannot tell a personal
 *  account login apart from a webhook — both arrive as `pairing`. Inventing a
 *  risk to make derived doors look like authored ones would be exactly the
 *  "never invent one to make a door look symmetrical" this file already
 *  forbids. */
export function transportedDoorBody(entry: TransportedChannelInput): string {
  const what = String(entry.selection_label || entry.label || "").trim();
  return entry.connect_method === "pairing"
    ? `${what}. Linked on this agent's computer — there is nothing to paste here.`
    : `${what}. Set up from here.`;
}

/** Group the transported channels a gateway carries into platforms, each with
 *  one door per variant. Order is preserved: platforms in the order their base
 *  variant first appears, variants in catalog order. */
export function groupTransportedChannels(entries: TransportedChannelInput[]): TransportedChannelPlatform[] {
  const groups: TransportedChannelInput[][] = [];
  for (const entry of entries) {
    const group = groups.find((members) => members.some((member) => sameChannelPlatform(member, entry)));
    if (group) group.push(entry);
    else groups.push([entry]);
  }

  return groups.map((members) => {
    // The base is the shortest id in the family — the platform every variant
    // extends. Ties cannot happen: a proper prefix is strictly shorter.
    const base = members.reduce((shortest, member) =>
      platformToken(member).length < platformToken(shortest).length ? member : shortest,
    );
    const ordered = [base, ...members.filter((member) => member !== base)];
    const platformLabel = String(base.label || "").trim();
    return {
      key: base.channel_key,
      label: platformLabel,
      iconKey: base.channel_key,
      variants: ordered,
      doors: ordered.map((variant) => ({
        key: variant.channel_key,
        label: transportedDoorLabel(variant, platformLabel),
        body: transportedDoorBody(variant),
        real: true,
        // EVERY transported door needs the agent's own computer, whichever
        // way it is set up — this used to read `connect_method === "pairing"`,
        // which understated it. A `credential` variant is pasted from here
        // rather than linked by hand on the box, but the thing the paste ends
        // up in is the transport's config ON that box: the catalog itself is
        // read off a gateway, the credential write is
        // `PUT .../gateways/{gateway_id}/channels/{key}/credential`, and
        // `remediationFor(..., hasGateway: false)` already answers
        // `needs_hardware` for every transported channel regardless of
        // connect_method. So "needs a computer" is a property of the LANE,
        // not of the individual channel — which is exactly why it can be
        // stated here once, for all of them, with no per-channel knowledge
        // and nothing to edit when the transport ships another one.
        //
        // Read by two things that already existed: the door face's hardware
        // note in the transported picker, and channel-hardware-tier.ts, which
        // derives the whole grid's two-tier split from this field rather than
        // from a list of channel names.
        requiresHardware: true,
      })),
    };
  });
}

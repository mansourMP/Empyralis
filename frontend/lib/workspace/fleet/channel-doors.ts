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

/** Each channel shows only the connection MODES that are real, safe, and built
 *  today for that platform — never a door that fails, and never a second mode
 *  standing in for one that doesn't exist yet. "Full account" doors bind to
 *  THIS agent's own preferred_gateway_id (see ChannelsTab's `agentGatewayId`),
 *  never to a workspace-wide/Sage-routed session. */
export const CHANNEL_DOORS: Record<string, ChannelDoor[]> = {
  sage_telegram_hosted: [
    {
      key: "byo_bot",
      label: "Chatbot",
      body: "People message a separate bot you create. This agent never touches your own Telegram account.",
      real: true,
      consequence: {
        tone: "safe",
        text: "Your own account can't be banned. Bots can't read every message in a group.",
      },
    },
    {
      key: "full_account",
      label: "Full account",
      body: "The agent signs in as you — phone, code, and 2FA if enabled — and sees exactly what you see.",
      real: true,
      requiresHardware: true,
      consequence: {
        tone: "risk",
        text: "Telegram can ban your number for automated use.",
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
  whatsapp_personal: [
    {
      key: "full_account",
      label: "Full account",
      body: "The agent signs in as you — scan a QR code or use a pairing code — and sees exactly what you see.",
      real: true,
      requiresHardware: true,
      consequence: {
        tone: "risk",
        text: "WhatsApp can ban your number for automated use.",
      },
    },
  ],
  signal_personal: [
    {
      key: "full_account",
      label: "Full account",
      body: "This agent's own Signal, via a signal-cli bridge on its computer. Requires a real signal-cli install — no cloud path.",
      real: true,
      requiresHardware: true,
    },
  ],
  imessage_personal: [
    {
      key: "full_account",
      label: "Full account",
      body: "This agent's own iMessage, via imsg — a small CLI that talks to Messages.app on the Mac it runs on.",
      real: true,
      requiresHardware: true,
    },
  ],
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

export function isChannelDoorAvailable(
  door: ChannelDoor,
  state: { hasHardware: boolean; doorConnected: boolean },
): boolean {
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
export function channelDoorUnavailableReason(door: ChannelDoor): string {
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
 *  the prefix names the transport without this module naming it. */
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
        // A pairing variant is linked ON the box by hand; a credential one is
        // pasted from here. Same question, same answer, on both paths.
        requiresHardware: variant.connect_method === "pairing",
      })),
    };
  });
}

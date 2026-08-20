/**
 * WHICH PLATFORM A CHANNEL IDENTIFIER NAMES — and the one rule that keeps a
 * platform to a single card across BOTH halves of the channel grid.
 *
 * ONE PLATFORM = ONE CARD is already enforced INSIDE the transported half
 * (channel-doors.ts's `groupTransportedChannels`, which collapses Zalo's three
 * channels into one card with three doors). It was never enforced ACROSS the
 * two halves, and the 2026-08-14 full OpenClaw cutover is what made that gap
 * visible: five platforms moved to the transport, so the transported grid grew
 * a Telegram card, a WhatsApp card, a Signal card, an iMessage card and a
 * Weixin card — and the first-party grid still contributed its own.
 *
 *     WHAT SHIPPED ON 2026-08-14                  WHAT THIS MODULE FIXES
 *       ▢ Telegram        (sage_telegram_hosted)    ▢ Telegram   (transported)
 *       ▢ Telegram        (openclaw_telegram)       ▢ Slack      (first-party)
 *       ▢ WeChat / WeCom  (wechat_official)         ▢ Discord    (first-party)
 *       ▢ Weixin          (openclaw_openclaw-…)     ▢ Weixin     (transported)
 *       ▢ WeCom           (openclaw_wecom)          ▢ WeCom      (transported)
 *
 * THE ASYMMETRY THAT CAUSED IT, STATED PLAINLY
 * --------------------------------------------
 * The cutover deleted a first-party card when it deleted that card's RUNTIME:
 * `whatsapp_personal`, `signal_personal` and `imessage_personal` went with the
 * Baileys / signal-cli / BlueBubbles runtimes. But a platform can have more
 * than one first-party lane, and the two survivors were on the OTHER lane:
 * `sage_telegram_hosted` is Telegram-the-hosted-bot (no gramjs runtime to
 * delete) and `wechat_official` is WeChat-the-Official-Account (no
 * local-bridge runtime to delete). Both platforms are in
 * `OPENCLAW_CUT_OVER_CHANNEL_IDS`; neither card was removed, because the
 * cutover reasoned per RUNTIME and the product law is per PLATFORM.
 *
 * So the rule here is per platform and is DERIVED, never a list:
 *
 *     a first-party grid card is dropped  ⟺  the transport's ACTIVE catalog
 *                                            already carries that platform
 *
 * The transported half of the grid is exactly the backend's active catalog
 * (`channel_lane_contract_service.OPENCLAW_ACTIVE_CHANNELS` — a channel the
 * transport OWNS, i.e. one it carries that no first-party runtime still owns).
 * So "the transport carries this platform" IS "this platform was cut over",
 * read off live data rather than transcribed. Slack and Discord keep their
 * first-party cards for free today, because those two channels are superseded
 * upstream and never enter the active catalog at all; the day either is cut
 * over, its first-party card disappears here with no edit.
 *
 * A hand-written list of "these ids are really duplicates" is the exact
 * mistake this surface has already made and corrected twice (CLAUDE.md: the
 * five-entry channel tuple, the parallel label map). There is none here.
 *
 * PURE DATA + PURE FUNCTIONS, IN ITS OWN MODULE
 * ---------------------------------------------
 * No React, no CSS import, no "use client" — the same reason channel-doors.ts
 * and openclaw-channel-copy.ts live apart from FleetAgentDetail.tsx:
 * channel-platform.test.ts runs under plain `npx tsx`, outside Next.js, and
 * imports the REAL first-party grid and the REAL generated manifest so the
 * expected set and the actual set come from different places.
 */

import { transportedPlatformToken, type TransportedChannelInput } from "./channel-doors";

/** Lane segments — they say HOW a platform is reached on this deployment, not
 *  WHICH platform it is. Mirrors channel_lane_contract_service's own
 *  `_FIRST_PARTY_LANE_SUFFIXES`, widened by the two lanes that survived the
 *  cutover (`_hosted`, `_official`) and the one the Studio catalog uses
 *  (`_business`). A segment here is only ever STRIPPED, never matched on, so
 *  an unknown lane suffix degrades to "no core token" — the card keeps its own
 *  card, i.e. today's behaviour — rather than to a wrong merge. */
const LANE_PREFIX_SEGMENTS = new Set(["sage"]);
const LANE_SUFFIX_SEGMENTS = new Set([
  "personal",
  "bot",
  "hosted",
  "official",
  "business",
  "twilio",
  "cloud",
  "api",
]);

/** Lanes that are a genuinely INDEPENDENT, hardware-free implementation of a
 *  platform, never a duplicate to be resolved away. `_personal`/`_twilio`
 *  name the account-pairing / SMS-carrier lanes OpenClaw's cutover set is
 *  actually about (see openclaw_channel_registry.py's own comment); `_hosted`
 *  and `_official` name a CLOUD BOT lane — sage_telegram_hosted,
 *  wechat_official — that needs no Agent Computer at all, exactly like
 *  discord_bot/slack/sms_twilio.
 *
 *  Found live, 2026-08-20: `OPENCLAW_CUT_OVER_CHANNEL_IDS` briefly listed
 *  "telegram" as a bare platform token to retire the deleted gramjs
 *  PERSONAL-account lane — but `resolve_transport_ownership` decides per
 *  PLATFORM, not per LANE, so the token also swept up `sage_telegram_hosted`,
 *  an unrelated, still-live, hardware-free lane. A fresh cloud-only agent's
 *  Channels tab showed Telegram as "Needs Gateway" with the actual paste-a-
 *  token flow unreachable — the exact "force an agent to acquire hardware it
 *  does not need" regression this module's own Discord/Slack precedent
 *  exists to prevent. Fixed at the source (openclaw_channel_registry.py no
 *  longer cuts telegram over) — this set is the SECOND, structural layer:
 *  even if a future active-catalog change re-introduces that same
 *  cross-lane collision for telegram, wechat, or a channel added later, a
 *  protected first-party card is never silently dropped for it. */
const PROTECTED_HARDWARE_FREE_LANE_SUFFIXES = new Set(["hosted", "official"]);

/** True when a first-party entry's own lane suffix (its LAST segment, before
 *  laneCore strips it) names a protected hardware-free bot lane. Checked on
 *  the ID directly, never the label — a label can be renamed without the
 *  lane changing, and the lane is what the guarantee is actually about. */
function hasProtectedHardwareFreeLane(entry: { id: string }): boolean {
  const segments = segmentsOf(entry.id);
  const last = segments[segments.length - 1];
  return last !== undefined && PROTECTED_HARDWARE_FREE_LANE_SUFFIXES.has(last);
}

/** Lowercase alphanumeric runs. Non-ASCII is dropped, so OpenClaw's own
 *  `WeCom（企业微信）` and `Weixin（微信）` reduce to `wecom` / `weixin` — their
 *  words, normalised, never re-typed here. */
function segmentsOf(value: string): string[] {
  return String(value || "")
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(Boolean);
}

/** The platform core of an identifier's segments: lane affixes removed from
 *  both ends. `sage_telegram_hosted` -> `telegram`, `wechat_official` ->
 *  `wechat`, `discord_bot` -> `discord`. Returns [] when nothing survives. */
function laneCore(segments: string[]): string[] {
  let start = 0;
  let end = segments.length;
  while (start < end && LANE_PREFIX_SEGMENTS.has(segments[start])) start += 1;
  while (end > start && LANE_SUFFIX_SEGMENTS.has(segments[end - 1])) end -= 1;
  return segments.slice(start, end);
}

/** A label can name more than one platform on one card (`WeChat / WeCom`), and
 *  each half is its own platform token. Split on the separators a label uses to
 *  mean "or", never on whitespace — `Google Chat` is one platform, not two. */
function labelTokens(label: string): string[] {
  return String(label || "")
    .split(/[/|·•]+/)
    .map((part) => segmentsOf(part).join(""))
    .filter((token) => token.length >= 2);
}

/** Every token that could name the platform behind a first-party grid entry:
 *  the whole id, its lane core, and each half of its label. */
export function firstPartyPlatformTokens(entry: FirstPartyChannelGridEntry): Set<string> {
  const segments = segmentsOf(entry.id);
  const tokens = new Set<string>([segments.join(""), laneCore(segments).join(""), ...labelTokens(entry.label)]);
  tokens.delete("");
  for (const token of Array.from(tokens)) if (token.length < 2) tokens.delete(token);
  return tokens;
}

/** Every token that could name the platform behind a transported card. Each
 *  variant contributes its transport-stripped channel token (the vendor-aware
 *  one channel-doors.ts already groups by — no module names the transport in
 *  order to strip it) plus its own display label. */
export function transportedPlatformTokens(platform: TransportedPlatformLike): Set<string> {
  const tokens = new Set<string>(labelTokens(platform.label));
  for (const variant of platform.variants) {
    tokens.add(transportedPlatformToken(variant));
    for (const token of labelTokens(variant.label)) tokens.add(token);
  }
  tokens.delete("");
  for (const token of Array.from(tokens)) if (token.length < 2) tokens.delete(token);
  return tokens;
}

/** Ordered platform-token candidates for a bare channel key, most specific
 *  first — used where only the key is in hand (an agent's bound `channel_key`,
 *  a transported card's `iconKey`) and there is no `channel_id` beside it to
 *  recover the transport's own namespace from.
 *
 *  Leading segments are dropped one at a time, so `openclaw_openclaw-weixin`
 *  yields `openclawopenclawweixin`, `openclawweixin`, `weixin`. That is only
 *  safe because every consumer requires the candidate to EXIST in its own
 *  table before using it — a junk candidate resolves to nothing rather than to
 *  something wrong. Do not use this for the dedupe rule above, which compares
 *  two token sets against each other and would have no such backstop. */
export function channelPlatformTokenCandidates(channelKey: string): string[] {
  const segments = segmentsOf(channelKey);
  const candidates: string[] = [];
  for (let index = 0; index < segments.length; index += 1) {
    const tail = segments.slice(index);
    candidates.push(tail.join(""), laneCore(tail).join(""));
  }
  return Array.from(new Set(candidates)).filter(Boolean);
}

/** The first-party half of the channel grid, as this module needs to see it. */
export type FirstPartyChannelGridEntry = { id: string; label: string };

/** The transported half, as this module needs to see it — the shape
 *  channel-doors.ts's `groupTransportedChannels` already returns. */
export type TransportedPlatformLike = {
  key: string;
  label: string;
  variants: TransportedChannelInput[];
};

export type UnifiedChannelGridPlan<
  F extends FirstPartyChannelGridEntry,
  T extends TransportedPlatformLike,
> = {
  /** The first-party entries that still belong on the grid. */
  firstParty: F[];
  /** The ones the transport now owns, each paired with the card that owns it.
   *  Returned rather than discarded so a caller (or a test) can say WHICH
   *  transported card superseded it, instead of a card silently vanishing. */
  superseded: { entry: F; carriedBy: T }[];
};

/** THE RULE. A first-party card whose platform the transport actively carries
 *  is not rendered — the transported card is the one card for that platform.
 *
 *  EXCEPT a protected hardware-free bot lane (`hasProtectedHardwareFreeLane`
 *  — `sage_telegram_hosted`, `wechat_official`), which never yields. Those
 *  lanes are not a duplicate of whatever the transport carries for the same
 *  platform; they are a genuinely different, strictly EASIER way to connect,
 *  the same category as discord_bot/slack/sms_twilio (which never collide in
 *  the first place because their OpenClaw equivalents are never active — see
 *  openclaw_channel_registry.py's OPENCLAW_CUT_OVER_CHANNEL_IDS). Backend
 *  policy is expected to already keep telegram/wechat off the active
 *  transported catalog for exactly this reason; this is the second,
 *  structural layer so a future active-catalog regression cannot silently
 *  make a computer mandatory for a channel that has never needed one. */
export function planUnifiedChannelGrid<
  F extends FirstPartyChannelGridEntry,
  T extends TransportedPlatformLike,
>(firstParty: readonly F[], transported: readonly T[]): UnifiedChannelGridPlan<F, T> {
  const carriers = transported.map((platform) => ({ platform, tokens: transportedPlatformTokens(platform) }));
  const kept: F[] = [];
  const superseded: { entry: F; carriedBy: T }[] = [];
  for (const entry of firstParty) {
    if (hasProtectedHardwareFreeLane(entry)) {
      kept.push(entry);
      continue;
    }
    const tokens = firstPartyPlatformTokens(entry);
    const carrier = carriers.find((candidate) =>
      Array.from(tokens).some((token) => candidate.tokens.has(token)),
    );
    if (carrier) superseded.push({ entry, carriedBy: carrier.platform });
    else kept.push(entry);
  }
  return { firstParty: kept, superseded };
}

/** Every group of grid cards that resolve to the SAME platform — i.e. every
 *  violation of ONE PLATFORM = ONE CARD, across both halves at once.
 *
 *  This is the assertion the 2026-08-14 duplicate cards would have failed. It
 *  reads the same token sets the rule above does, so a dedupe that stops
 *  matching (a renamed label, a new lane suffix) surfaces here as a collision
 *  rather than as two cards nobody notices until they are on screen.
 *
 *  A protected hardware-free first-party lane (`hasProtectedHardwareFreeLane`
 *  — same exemption `planUnifiedChannelGrid` grants) is excluded from the
 *  sweep entirely: two cards for one platform is a real defect ONLY when
 *  neither of them is a settled, intentional survivor. A protected lane
 *  colliding with whatever the transport carries for the same platform is
 *  exactly that settled case (`sage_telegram_hosted` beside a manifest that
 *  still lists a `telegram` channel) — asserted as fine, not ignored by
 *  omission, so a future protected entry that stops resolving to a real
 *  first-party card would still be caught by every OTHER assertion in this
 *  file. */
export function channelGridPlatformCollisions(
  firstParty: readonly FirstPartyChannelGridEntry[],
  transported: readonly TransportedPlatformLike[],
): string[][] {
  const cards = [
    ...firstParty.filter((entry) => !hasProtectedHardwareFreeLane(entry)).map((entry) => ({ label: entry.label, tokens: firstPartyPlatformTokens(entry) })),
    ...transported.map((platform) => ({ label: platform.label, tokens: transportedPlatformTokens(platform) })),
  ];
  const groups: { labels: string[]; tokens: Set<string> }[] = [];
  for (const card of cards) {
    const group = groups.find((candidate) =>
      Array.from(card.tokens).some((token) => candidate.tokens.has(token)),
    );
    if (group) {
      group.labels.push(card.label);
      for (const token of card.tokens) group.tokens.add(token);
    } else {
      groups.push({ labels: [card.label], tokens: new Set(card.tokens) });
    }
  }
  return groups.filter((group) => group.labels.length > 1).map((group) => group.labels);
}

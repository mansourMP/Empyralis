/**
 * The order the channel grid leads with.
 *
 * Alphabetical put ClickClack above Discord and Nostr above WhatsApp — a grid
 * whose first row is nobody's messenger. So the grid leads with the messengers
 * people actually use.
 *
 * AN AUTHORED RANKING IS NOT AN AUTHORED CHANNEL LIST
 * ---------------------------------------------------
 * This codebase has twice made — and twice corrected — the mistake of typing
 * out WHICH CHANNELS EXIST (the five-entry tuple, the parallel label map). That
 * list is derived now, from the transport's own registry, precisely because a
 * hand-written one goes stale the day upstream ships a channel. This table is a
 * different kind of thing and the difference is the whole reason it is allowed:
 *
 *     A CHANNEL LIST          answers "what exists" — derivable, so derive it.
 *                             A missing entry means a channel is INVISIBLE.
 *     A POPULARITY RANKING    answers "which of these do most people use" — a
 *                             product judgment that exists in no data we hold.
 *                             A missing entry means a channel sorts LOWER.
 *
 * Hence the one rule that keeps it from rotting: this table is a PREFIX, never
 * a membership test. Ranked channels come first, in this order; everything else
 * — including every channel upstream ships tomorrow — falls to the bottom and
 * sorts alphabetically among itself, with no code change. That is asserted with
 * a synthetic channel in openclaw-channel-copy.test.ts, since a channel upstream
 * has not shipped is the only way to prove the claim.
 *
 * The head of the list is the founder's own call (WhatsApp, Telegram, WeChat,
 * Discord, Signal, iMessage, Slack); the tail is ordered by rough global reach.
 * Reordering it is a product decision and needs nothing else changed.
 */

/** Normalised platform keys, most-used first. Lower index = earlier in the grid.
 *
 *  Keys are normalised labels (lowercased, non-alphanumerics dropped) rather
 *  than backend ids, because the two card families that share this grid have no
 *  id space in common — a first-party card is `whatsapp_personal`, a transported
 *  one is `openclaw_whatsapp`, and only the name on the card is the same fact.
 *  Both the WHOLE label and its LEADING WORD are looked up, which is what lets
 *  "WeChat / WeCom" match `wechat` without anyone writing `wechatwecom`. */
export const CHANNEL_POPULARITY_ORDER: readonly string[] = [
  "whatsapp",
  "telegram",
  "wechat", // the first-party card, labelled "WeChat / WeCom"
  "weixin", // consumer WeChat, under the transport's own name for it
  "wecom", // WeChat Work — a different product, so a separate rank
  "discord",
  "signal",
  "imessage",
  "slack",
  "microsoftteams",
  "line",
  "qqbot",
  "zalo",
  "feishu",
  "matrix",
  "mattermost",
  "googlechat",
  "twitch",
  "nextcloudtalk",
];

const RANK_BY_KEY = new Map(CHANNEL_POPULARITY_ORDER.map((key, index) => [key, index]));

function normalise(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, "");
}

/** The keys one label can be ranked under, most specific first: the whole label,
 *  then its leading word. "Microsoft Teams" is `microsoftteams` before it is
 *  `microsoft`, so a vendor word can never quietly rank a product nobody ranked
 *  — the leading word only decides anything when the whole label misses. */
export function channelPopularityKeys(label: string): string[] {
  const trimmed = String(label || "").trim();
  const whole = normalise(trimmed);
  const leading = normalise(trimmed.split(/[\s/]+/)[0] || "");
  return leading && leading !== whole ? [whole, leading] : [whole];
}

/** Position in the ranking, or +Infinity for anything unranked — which is every
 *  channel this table has never heard of, by design. */
export function channelPopularityRank(label: string): number {
  for (const key of channelPopularityKeys(label)) {
    const rank = RANK_BY_KEY.get(key);
    if (rank !== undefined) return rank;
  }
  return Number.POSITIVE_INFINITY;
}

/** Grid order: ranked channels in ranked order, then everything else
 *  alphabetically. Comparing the ranks by `<` rather than subtracting them is
 *  load-bearing — two unranked channels are both +Infinity and `Infinity -
 *  Infinity` is NaN, which Array.prototype.sort reads as "equal" on some pairs
 *  and leaves the tail in a non-deterministic order. */
export function compareChannelsByPopularity(a: { label: string }, b: { label: string }): number {
  const rankA = channelPopularityRank(a.label);
  const rankB = channelPopularityRank(b.label);
  if (rankA !== rankB) return rankA < rankB ? -1 : 1;
  return a.label.localeCompare(b.label);
}

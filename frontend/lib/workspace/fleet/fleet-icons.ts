// Icon paths for the platform/connector grids. The backend catalog has no
// image field (confirmed by reading connection_catalog_service.py) — brand
// icons are a client-side lookup by id, matching the same static assets the
// workspace-wide Channels/Connectors pages use.

// Bump when a brand asset is added or changed. Appended as ?v= to every icon
// URL below so a browser that once cached a 404 for a not-yet-deployed asset
// (Safari renders that as a "?" broken-image box, and negatively caches it)
// is forced to fetch the new URL fresh instead of reusing the poisoned entry.
// This is why a channel logo could stay broken for a user across normal
// refreshes even after the file went live — the fix is a new URL, not a reload.
const ASSET_VERSION = "20260810";

const withVersion = (map: Record<string, string>): Record<string, string> =>
  Object.fromEntries(
    Object.entries(map).map(([key, path]) => [key, `${path}?v=${ASSET_VERSION}`]),
  );

const RAW_CHANNEL_ICONS: Record<string, string> = {
  sage_telegram_hosted: "/brand-assets/channels/telegram.svg",
  telegram_personal: "/brand-assets/channels/telegram.svg",
  telegram_bot: "/brand-assets/channels/telegram.svg",
  slack: "/brand-assets/channels/slack.svg",
  discord_bot: "/brand-assets/channels/discord.svg",
  whatsapp_personal: "/brand-assets/channels/whatsapp.svg",
  whatsapp_twilio: "/brand-assets/channels/whatsapp.svg",
  signal_personal: "/brand-assets/channels/signal.svg",
  imessage_personal: "/brand-assets/channels/imessage.svg",
  wechat_personal: "/brand-assets/channels/wechat.svg",
  wechat_official: "/brand-assets/channels/wechat.svg",
  apple_messages_business: "/brand-assets/channels/imessage.svg",
  email: "/brand-assets/generic/email.svg",

  // ── Transported (OpenClaw) channels ───────────────────────────────────────
  // Keyed by `channel_key` verbatim (`openclaw_<their channel id>`), so the
  // lookup is the same one-liner every other card uses and nothing here has to
  // know what a channel IS. Adding an OpenClaw channel still needs no code
  // change: an id with no entry falls through to the neutral monogram tile,
  // which is the correct rendering for "we have no licensed mark for that one".
  //
  // Provenance, per mark. This table is the record that makes shipping these
  // defensible; do not add a row without one. Priority order followed was the
  // brand's own press/brand page > official developer documentation > a
  // Wikimedia/CC0 file whose licence was actually read. Nothing here is drawn,
  // traced, guessed or approximated, and no mark's geometry, proportions or
  // colours were altered — the only edits made were cropping an official
  // lockup down to its own icon element and dropping page-background cruft.
  //
  //   feishu      feishu.png   Wikimedia Commons File:Lark_Suite_logo_2022.png,
  //                            {{PD-textlogo}} (below the threshold of
  //                            originality) + {{Trademarked}}; nominative use.
  //                            PNG because ByteDance publishes no vector and no
  //                            brand page at all. Downscaled 792->128px only.
  //   googlechat  googlechat.svg  simple-icons (CC0-1.0), vectorised from
  //                            support.google.com/chat/answer/9455386.
  //   line        line.svg     OFFICIAL: LY Corporation's own brand-icon vector
  //                            at line.me/en/logo (LINE_Brand_icon_RGB.ai).
  //                            Their guidelines demand the icon be used as-is,
  //                            which is exactly why the official file is used
  //                            here and not a monochrome redraw of it.
  //   matrix      matrix.svg   simple-icons (CC0-1.0), source matrix.org.
  //   mattermost  mattermost.svg simple-icons (CC0-1.0), source
  //                            mattermost.org/brand-guidelines/.
  //   msteams     msteams.svg  Wikimedia Commons File:Microsoft_Office_Teams_
  //                            (2025-present).svg, {{PD-textlogo}} +
  //                            {{Trademarked}}. Microsoft had simple-icons drop
  //                            its marks, so no CC0 vectorisation exists; the
  //                            Commons file's own licence is the basis and the
  //                            use is nominative.
  //   nextcloud-talk  nextcloud.svg  simple-icons (CC0-1.0), source
  //                            nextcloud.com/press/. The Nextcloud platform
  //                            mark — Talk's own app icon is AGPL-3.0 inside
  //                            nextcloud/spreed, and a copyleft asset is not
  //                            something to drag into a closed product.
  //   nostr       nostr.svg    OFFICIAL: the Nostr logo pack,
  //                            github.com/mbarulli/nostr-logo, CC0-1.0.
  //   qqbot       qq.svg       simple-icons (CC0-1.0), guidelines
  //                            qq.design/brand/BrandDesign/Logo.
  //   tlon        tlon.svg     OFFICIAL: Tlon's own app icon in
  //                            github.com/tloncorp/tlon-apps, MIT.
  //   twitch      twitch.svg   simple-icons (CC0-1.0), source brand.twitch.tv.
  //   wecom       wecom.svg    OFFICIAL: Tencent's developer design-resource
  //                            download (developer.work.weixin.qq.com/document/
  //                            path/90306 -> wwopen/downloadfile/logo.zip),
  //                            published for third-party integrators. Cropped
  //                            from the lockup to the icon element.
  //   zalo/zalouser/zaloclawbot  zalo.svg  simple-icons (CC0-1.0), source
  //                            zalo.me. All three are the same platform, the
  //                            same way telegram_bot and telegram_personal
  //                            already share one mark.
  //   clickclack  clickclack.svg  OFFICIAL: the project's own icon in
  //                            github.com/openclaw/clickclack, MIT.
  //
  // Deliberately ABSENT, and each stays a neutral monogram tile until this
  // changes — a lookalike is worse than no mark:
  //   irc      IRC is a 1988 protocol with no owner and no official mark.
  //            There is nothing to source. Do not substitute a client's logo.
  //   yuanbao  Tencent Yuanbao publishes no brand/press page and no free-
  //            licensed file exists (nothing on Commons; the third-party icon
  //            sets that carry it are redraws, which the founder ruled out).
  //   synology-chat  Licence is fine (simple-icons, CC0) — the MARK is wrong
  //            for this size. Synology publishes only a WORDMARK, confirmed on
  //            their own branding page ("Standard / Gray / Black / Reversion",
  //            no symbol), and their guidelines forbid modifying it, so a
  //            symbol cannot be cropped out of it either. At 32px it rendered
  //            as illegible grey mush in both themes; shipped as a monogram
  //            instead, because a smear is worse than a letter. Revisit only
  //            if Synology publishes an icon-only mark.
  "openclaw_clickclack": "/brand-assets/channels/clickclack.svg",
  "openclaw_feishu": "/brand-assets/channels/feishu.png",
  "openclaw_googlechat": "/brand-assets/channels/googlechat.svg",
  "openclaw_line": "/brand-assets/channels/line.svg",
  "openclaw_matrix": "/brand-assets/channels/matrix.svg",
  "openclaw_mattermost": "/brand-assets/channels/mattermost.svg",
  "openclaw_msteams": "/brand-assets/channels/msteams.svg",
  "openclaw_nextcloud-talk": "/brand-assets/channels/nextcloud.svg",
  "openclaw_nostr": "/brand-assets/channels/nostr.svg",
  "openclaw_openclaw-zaloclawbot": "/brand-assets/channels/zalo.svg",
  "openclaw_qqbot": "/brand-assets/channels/qq.svg",
  "openclaw_tlon": "/brand-assets/channels/tlon.svg",
  "openclaw_twitch": "/brand-assets/channels/twitch.svg",
  "openclaw_wecom": "/brand-assets/channels/wecom.svg",
  "openclaw_zalo": "/brand-assets/channels/zalo.svg",
  "openclaw_zalouser": "/brand-assets/channels/zalo.svg",
};

const RAW_CONNECTOR_ICONS: Record<string, string> = {
  // google_workspace grants Gmail + Calendar + Drive in one connection (see
  // connection_catalog_service.py connector_ids=[...]) so it gets the Google
  // "G" mark, not the Gmail-only glyph gmail.svg still used for the literal
  // Gmail chip on the marketing landing page.
  google_workspace: "/brand-assets/apps/google.svg",
  microsoft_365: "/brand-assets/apps/microsoft365.svg",
  github: "/brand-assets/apps/github.svg",
  notion: "/brand-assets/apps/notion.svg",
  linear: "/brand-assets/apps/linear.svg",
  dropbox: "/brand-assets/apps/dropbox.svg",
  figma: "/brand-assets/apps/figma.svg",
  todoist: "/brand-assets/apps/todoist.svg",
  airtable: "/brand-assets/apps/airtable.svg",
  canva: "/brand-assets/apps/canva.svg",
  asana: "/brand-assets/apps/asana.svg",
  hubspot: "/brand-assets/apps/hubspot.svg",
  zoom: "/brand-assets/apps/zoom.svg",
  calendly: "/brand-assets/apps/calendly.svg",
  clickup: "/brand-assets/apps/clickup.svg",
  jira: "/brand-assets/apps/jira.svg",
  stripe: "/brand-assets/apps/stripe.svg",
  salesforce: "/brand-assets/apps/salesforce.svg",
  webflow: "/brand-assets/apps/webflow.svg",
  monday: "/brand-assets/apps/monday.svg",
  box: "/brand-assets/apps/box.svg",
  gitlab: "/brand-assets/apps/gitlab.svg",
  confluence: "/brand-assets/apps/confluence.svg",
  miro: "/brand-assets/apps/miro.svg",
  intercom: "/brand-assets/apps/intercom.svg",
  docusign: "/brand-assets/apps/docusign.svg",
  square: "/brand-assets/apps/square.svg",
  typeform: "/brand-assets/apps/typeform.svg",
  vercel: "/brand-assets/apps/vercel.svg",
  higgsfield: "/brand-assets/apps/higgsfield.svg",
  shopify: "/brand-assets/apps/shopify.svg",
  zapier: "/brand-assets/apps/zapier.svg",
  s3: "/brand-assets/apps/aws-s3.svg",
  smtp: "/brand-assets/generic/email.svg",
  wechat_work: "/brand-assets/channels/wechat.svg",
  instagram_business: "/brand-assets/apps/instagram.svg",
  webhook: "/brand-assets/generic/webhook.svg",
  // 2026-07-19 connector wiring (40 Tier-1 verified DCR connectors, minus
  // sentry/paypal/attio skipped as exact duplicates of already-wired
  // connectors above). Real Simple Icons brand SVGs where a verified slug
  // exists; a neutral monogram placeholder (see the SVG's own <!-- --> note)
  // for the rest, including "fathom" (Simple Icons' "fathom" slug is the
  // WRONG brand, Fathom Analytics) and "klaviyo" (the directory's assumed
  // slug does not actually exist in Simple Icons -- verified live).
  netlify: "/brand-assets/apps/netlify.svg",
  supabase: "/brand-assets/apps/supabase.svg",
  planetscale: "/brand-assets/apps/planetscale.svg",
  neon: "/brand-assets/apps/neon.svg",
  railway: "/brand-assets/apps/railway.svg",
  replit: "/brand-assets/apps/replit.svg",
  postman: "/brand-assets/apps/postman.svg",
  buildkite: "/brand-assets/apps/buildkite.svg",
  socket: "/brand-assets/apps/socket.svg",
  brex: "/brand-assets/apps/brex.svg",
  robinhood: "/brand-assets/apps/robinhood.svg",
  mixpanel: "/brand-assets/apps/mixpanel.svg",
  posthog: "/brand-assets/apps/posthog.svg",
  meta_ads: "/brand-assets/apps/meta.svg",
  semrush: "/brand-assets/apps/semrush.svg",
  coda: "/brand-assets/apps/coda.svg",
  gusto: "/brand-assets/apps/gusto.svg",
  deel: "/brand-assets/apps/deel.svg",
  remote_com: "/brand-assets/apps/remote.svg",
  ashby: "/brand-assets/apps/ashby.svg",
  klaviyo: "/brand-assets/apps/klaviyo.svg",
  customer_io: "/brand-assets/apps/customer-io.svg",
  heroku: "/brand-assets/apps/heroku.svg",
  sourcegraph: "/brand-assets/apps/sourcegraph.svg",
  whimsical: "/brand-assets/apps/whimsical.svg",
  ramp: "/brand-assets/apps/ramp.svg",
  mercury: "/brand-assets/apps/mercury.svg",
  amplitude: "/brand-assets/apps/amplitude.svg",
  ahrefs: "/brand-assets/apps/ahrefs.svg",
  close_crm: "/brand-assets/apps/close.svg",
  apollo_io: "/brand-assets/apps/apollo.svg",
  outreach: "/brand-assets/apps/outreach.svg",
  salesloft: "/brand-assets/apps/salesloft.svg",
  clay: "/brand-assets/apps/clay.svg",
  fireflies: "/brand-assets/apps/fireflies.svg",
  fathom: "/brand-assets/apps/fathom.svg",
  // 2026-07-31 connector-picker logo audit (MAN-145 founder feedback: "some
  // apps doesnt even have image logo"). Cross-checked the full 76-item
  // work_app_connector lane in connection_catalog_service.py against this
  // map — these five ids were the only gaps left, closing coverage to
  // 76/76. Real Simple Icons brand SVGs (verified live against
  // simple-icons@latest) for paypal/sentry/cloudflare; "attio" has no
  // verified slug in Simple Icons (checked live, 404) so it gets the same
  // neutral monogram placeholder treatment as ahrefs/fathom/klaviyo/mercury
  // above, not a guessed logo. "email" (the generic OAuth-mailbox
  // connector, distinct from "smtp") reuses the same generic mail glyph
  // "smtp" already does — there's no brand to represent, it's a mail icon.
  paypal: "/brand-assets/apps/paypal.svg",
  sentry: "/brand-assets/apps/sentry.svg",
  cloudflare: "/brand-assets/apps/cloudflare.svg",
  attio: "/brand-assets/apps/attio.svg",
  email: "/brand-assets/generic/email.svg",
};

// Versioned public exports — see ASSET_VERSION above. Every render site
// (channel grid tiles, the expanded-channel door header, the connector
// picker) reads from these, so all icon URLs carry the cache-bust with no
// per-call-site changes.
export const CHANNEL_ICONS: Record<string, string> = withVersion(RAW_CHANNEL_ICONS);
export const CONNECTOR_ICONS: Record<string, string> = withVersion(RAW_CONNECTOR_ICONS);

// Short human label per channel key, keyed the same as CHANNEL_ICONS above —
// used wherever a channel key needs a name for accessibility (an icon's
// title/tooltip) rather than the full connect-flow copy CHANNEL_GRID_PLATFORMS
// (FleetAgentDetail.tsx) carries per platform.
export const CHANNEL_LABELS: Record<string, string> = {
  sage_telegram_hosted: "Telegram",
  telegram_personal: "Telegram",
  telegram_bot: "Telegram",
  slack: "Slack",
  discord_bot: "Discord",
  whatsapp_personal: "WhatsApp",
  whatsapp_twilio: "WhatsApp",
  signal_personal: "Signal",
  imessage_personal: "iMessage",
  wechat_personal: "WeChat",
  wechat_official: "WeChat / WeCom",
  apple_messages_business: "iMessage",
  email: "Email",
};

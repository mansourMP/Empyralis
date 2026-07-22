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
const ASSET_VERSION = "20260722";

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

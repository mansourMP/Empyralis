// Icon paths for the platform/connector grids. The backend catalog has no
// image field (confirmed by reading connection_catalog_service.py) — brand
// icons are a client-side lookup by id, matching the same static assets the
// workspace-wide Channels/Connectors pages use.

export const CHANNEL_ICONS: Record<string, string> = {
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
  apple_messages_business: "/brand-assets/channels/imessage.svg",
  email: "/brand-assets/generic/email.svg",
};

export const CONNECTOR_ICONS: Record<string, string> = {
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
};

# OAuth Provider Setup Runbook

Operational checklist for registering OAuth apps with third-party providers so
agents can connect to them. This is the "go do it" doc — for the underlying
audit (why this is needed, what's configured today) see Linear MAN-111/MAN-112.

**Source of truth for everything below:** `OAUTH_PROVIDER_CONFIGS` in
`server_modules/connection_oauth_service.py` (scopes, env var names) and the
callback route at `server_modules/routes_connections.py:564`
(`GET /connections/oauth/{provider}/callback`, mounted under `/api` in
`server.py:388`). Redirect URIs below use `https://empyralis.ai` per
`docs/DEPLOY-RUNBOOK.md`.

**All redirect URIs are static** — one per provider, independent of workspace
or agent. `callback_url()` (`connection_oauth_service.py:2193`) builds
`{origin}/api/connections/oauth/{provider}/callback` from the request origin
alone.

---

## Read this first — the provider list changed since MAN-111

MAN-111/MAN-112 named 18 providers as needing manual OAuth-app registration.
Re-checking the actual code (a "2026-07-19 connector sweep," documented
inline above each entry in `OAUTH_PROVIDER_CONFIGS`) shows **8 of those 18
now self-register via RFC 7591 Dynamic Client Registration and need zero
manual setup** — the sweep added live `registration_endpoint` values for them
after the original audit. Don't waste time creating dev-console apps for
these:

- **Figma**, **GitLab**, **Webflow**, **Monday.com**, **Square**,
  **Typeform**, **Vercel** — confirmed live DCR, fully automatic
  (`dynamic_registration_opt_in_required=False`), same zero-config tier as
  Linear/Notion/Stripe/etc.
- **Atlassian (Jira + Confluence)** — also DCR-capable and coded the same
  way, **but flagged unverified** in the code's own comments
  (`connection_oauth_service.py:628-636` and `:859-865`): a third-party
  report claims Atlassian's Remote MCP Beta can register a client via DCR
  and still reject it at the authorize/consent step. Try connecting Jira or
  Confluence for real before assuming it works — if it fails at consent,
  fall back to manually registering an OAuth 2.0 (3LO) app at
  `https://developer.atlassian.com/console/myapps/` and setting
  `ATLASSIAN_CLIENT_ID` / `ATLASSIAN_CLIENT_SECRET` (shared by both Jira and
  Confluence; register **both** callback URLs on one app: `.../oauth/jira/callback`
  and `.../oauth/confluence/callback`).

That leaves **10 providers that genuinely still require you to manually
register an OAuth app and set env vars** — those are the checklists below.

Separately (not covered here, no action needed): **Linear, Notion, Stripe,
Asana, Canva, Airtable, ClickUp, Higgsfield** self-register via DCR the same
way — this was already known before MAN-111.

| Still needs manual registration (10) | Confirmed self-registering, no action (8 newly found + 8 previously known = 16) |
|---|---|
| Google Workspace, GitHub, Microsoft 365, Slack, Discord, Zoom, Box, Salesforce, HubSpot, DocuSign | Figma, GitLab, Webflow, Monday, Square, Typeform, Vercel, Atlassian (Jira+Confluence, unverified at consent step) + Linear, Notion, Stripe, Asana, Canva, Airtable, ClickUp, Higgsfield |

---

## Google Workspace

- [ ] Go to **https://console.cloud.google.com/apis/credentials** (create/select a GCP project first if needed)
- [ ] Configure the OAuth consent screen if not already done (External user type, add scopes below)
- [ ] Create Credentials → **OAuth client ID** → Application type **Web application**
- [ ] Redirect URI: `https://empyralis.ai/api/connections/oauth/google_workspace/callback`
- [ ] Scopes to request (must match code exactly, `connection_oauth_service.py:114-120`):
  - `openid`
  - `email`
  - `profile`
  - `https://www.googleapis.com/auth/gmail.modify`
  - `https://www.googleapis.com/auth/calendar`
- [ ] Set env vars: `GOOGLE_WORKSPACE_OAUTH_CLIENT_ID`, `GOOGLE_WORKSPACE_OAUTH_CLIENT_SECRET`
  (code also accepts `GOOGLE_OAUTH_CLIENT_ID`/`_SECRET` or `GOOGLE_CLIENT_ID`/`_SECRET` as fallbacks —
  the stray existing `GOOGLE_OAUTH_CLIENT_ID` env var lines up with this fallback but has no matching
  secret set today)
- [ ] **Verification review required.** `gmail.modify` and `calendar` are Google-classified
  "sensitive" scopes — the OAuth consent screen needs Google's verification review before the app
  can serve non-test users at scale (days-to-weeks, not instant). The privacy policy / terms pages
  Google's review will check are already live at `frontend/app/privacy/page.tsx` and
  `frontend/app/terms/page.tsx` (confirmed by `test_google_oauth_verification_readiness.py`) — nothing
  to write, just submit for review.
- [ ] Optional: Drive access is opt-in, not requested by default — only add
  `https://www.googleapis.com/auth/drive.file` if you also set
  `GOOGLE_WORKSPACE_ENABLE_DRIVE_SCOPE=1` (or `GOOGLE_OAUTH_ENABLE_DRIVE_SCOPE=1`)

## GitHub

- [ ] Go to **https://github.com/settings/developers** → OAuth Apps → **New OAuth App**
- [ ] Authorization callback URL: `https://empyralis.ai/api/connections/oauth/github/callback`
- [ ] Scopes: GitHub OAuth Apps don't have a console scope picker — the scopes below are requested
  at authorize time by the code (`connection_oauth_service.py:141`), nothing to configure on GitHub's side:
  `repo`, `read:user`, `user:email`
- [ ] Set env vars: `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET`
  (fallback: `GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET`)
- [ ] No verification/review step — classic OAuth Apps activate immediately

## Microsoft 365

- [ ] Go to **https://entra.microsoft.com/** → Identity → Applications → App registrations →
  **New registration** (this is the current name for what used to be the Azure AD blade in
  `portal.azure.com` — flag: verify this URL still resolves the same way when you get there,
  Microsoft renames this UI often)
- [ ] Redirect URI (platform: Web): `https://empyralis.ai/api/connections/oauth/microsoft_365/callback`
- [ ] API permissions (delegated, Microsoft Graph) — must match `connection_oauth_service.py:166-173`:
  `offline_access`, `User.Read`, `Mail.ReadWrite`, `Mail.Send`, `Calendars.ReadWrite`, `Files.ReadWrite.All`
- [ ] Create a client secret under **Certificates & secrets**
- [ ] Set env vars: `MICROSOFT_365_OAUTH_CLIENT_ID`, `MICROSOFT_365_OAUTH_CLIENT_SECRET`
  (fallback: `MICROSOFT_OAUTH_CLIENT_ID`/`_SECRET`, `MICROSOFT_CLIENT_ID`/`_SECRET`)
- [ ] Optional: `MICROSOFT_365_OAUTH_TENANT_ID` — defaults to `common` (any Microsoft account/org) if unset
- [ ] **May require admin consent.** `Mail.ReadWrite`, `Files.ReadWrite.All`, and `Calendars.ReadWrite`
  are broad delegated permissions; depending on the connecting user's tenant policy, an org admin may
  need to grant consent before a user in that org can complete the OAuth flow. Not a review process
  Mansur runs — it's per-customer-tenant, flag it to customers if they hit it.

## Slack

- [ ] Go to **https://api.slack.com/apps** → **Create New App** → From scratch
- [ ] OAuth & Permissions → Redirect URLs → add `https://empyralis.ai/api/connections/oauth/slack/callback`
- [ ] Bot Token Scopes — must match `slack_connector.DEFAULT_SLACK_BOT_SCOPES`
  (`server_modules/connectors/slack_connector.py:20-33`):
  `app_mentions:read`, `channels:history`, `channels:read`, `chat:write`, `files:write`,
  `groups:history`, `groups:read`, `im:history`, `im:read`, `im:write`, `mpim:history`,
  `reactions:read`, `users:read`
- [ ] Set env vars: `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`
- [ ] No Slack app-directory review needed for single/internal-workspace installs; only needed if you
  submit the app for public Slack App Directory listing (not required here)

## Discord

- [ ] Go to **https://discord.com/developers/applications** → **New Application**
- [ ] OAuth2 → Redirects → add `https://empyralis.ai/api/connections/oauth/discord/callback`
- [ ] Scopes requested by the code (`connection_oauth_service.py:309`): `bot`, `identify`, plus a
  fixed bot-permissions integer (`274877908992`) sent as `permissions` in the auth URL — nothing to
  pick manually, just create the Bot user under the **Bot** tab
- [ ] Set env vars: `EMPYRALIS_DISCORD_APPLICATION_ID` (or `DISCORD_CLIENT_ID`), `DISCORD_CLIENT_SECRET`
- [ ] No review needed under 100 servers / without privileged intents. If any agent's bot ends up in
  100+ guilds, Discord requires a verification application at that point — not relevant yet.

## Zoom

- [ ] Go to **https://marketplace.zoom.us/develop/create** → **General App** (OAuth)
- [ ] Redirect URL for OAuth: `https://empyralis.ai/api/connections/oauth/zoom/callback`
- [ ] Scopes: **the code sends no explicit `scope` parameter at all**
  (`connection_oauth_service.py:528`, `scopes=()`) — Zoom's modern General App model grants whatever
  scopes you add in the app's own **Scopes** tab, not via the authorize URL. Add at minimum
  `user:read` (used by the connector's own profile check, `https://api.zoom.us/v2/users/me`); add
  meeting/recording scopes too if the agent needs to act on meetings — the exact minimum isn't
  pinned down in code, this is a judgment call on your side.
- [ ] Set env vars: `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET`
- [ ] General Apps activate immediately for your own account; only needed if you submit to Zoom App
  Marketplace for public listing (not required here)

## Box

- [ ] Go to **https://app.box.com/developers/console** → **Create New App** → Custom App →
  **OAuth 2.0 (User Authentication)**
- [ ] Redirect URI: `https://empyralis.ai/api/connections/oauth/box/callback`
- [ ] Scopes: like Zoom, **the code sends no explicit `scope` parameter**
  (`connection_oauth_service.py:795`, `scopes=()`) — Box grants whatever's checked under the app's
  own **Configuration → Application Scopes** tab. Check at least "Read all files and folders stored
  in Box" (or narrower, per what the agent actually needs).
- [ ] Set env vars: `BOX_CLIENT_ID`, `BOX_CLIENT_SECRET`
- [ ] No review needed for a Custom App used by your own enterprise/account

## Salesforce

- [ ] Log into the target Salesforce org → **Setup** (gear icon) → search **App Manager** →
  **New Connected App**
- [ ] Enable OAuth Settings → Callback URL: `https://empyralis.ai/api/connections/oauth/salesforce/callback`
- [ ] Selected OAuth Scopes — must match `connection_oauth_service.py:708`: `openid`, `api`, `refresh_token`
- [ ] Set env vars: `SALESFORCE_CLIENT_ID`, `SALESFORCE_CLIENT_SECRET`
- [ ] **Domain matters:** the code always calls `https://login.salesforce.com/...` (production login
  host, `connection_oauth_service.py:709-710`), never `test.salesforce.com`. Register the Connected
  App in a production/Developer Edition org reachable via `login.salesforce.com`, not a sandbox that
  only responds on `test.salesforce.com`, or the OAuth flow will 404/reject.
- [ ] No formal review needed for an internal Connected App (review is only for AppExchange listing)

## HubSpot

- [ ] Go to **https://developers.hubspot.com/** → sign in / create a developer account → **Apps** →
  **Create app** (flag: HubSpot's exact create-app path inside their developer portal shifts around
  their UI periodically — if this link lands somewhere different, look for "Apps" under your developer
  account's main nav)
- [ ] Auth tab → Redirect URL: `https://empyralis.ai/api/connections/oauth/hubspot/callback`
- [ ] Scopes — must match `connection_oauth_service.py:497-503`: `oauth`, `crm.objects.contacts.read`,
  `crm.objects.contacts.write`, `crm.objects.companies.read`, `crm.objects.deals.read`
- [ ] Set env vars: `HUBSPOT_CLIENT_ID`, `HUBSPOT_CLIENT_SECRET`
- [ ] No review needed to use the app on your own connected HubSpot accounts; review is only required
  to list publicly on the HubSpot App Marketplace (not needed here)

## DocuSign

- [ ] Go to **https://admin.docusign.com/** (or via `https://developers.docusign.com/` → Apps and Keys)
  → **Add App and Integration Key**
- [ ] Redirect URI: `https://empyralis.ai/api/connections/oauth/docusign/callback`
- [ ] Scopes — must match `connection_oauth_service.py:966`: `signature`, `extended`
- [ ] Set env vars: `DOCUSIGN_CLIENT_ID`, `DOCUSIGN_CLIENT_SECRET`
- [ ] **This is the slowest one on this list.** The code's `auth_url`/`token_url` point at
  `account.docusign.com` — DocuSign's **production** identity host, not the demo/sandbox
  (`account-d.docusign.com`). DocuSign requires a formal **"Go-Live"** review before an integration
  key can authenticate real (non-demo) accounts in production — this is a submission + approval
  process, not instant. Start this one first if the timeline matters. (Flagging this as based on
  DocuSign's well-known general process, not something confirmed against this repo's code — verify
  current Go-Live requirements directly on DocuSign's developer site before you start.)

---

## Summary — verification/review overhead by provider

| Provider | Extra review needed? | Notes |
|---|---|---|
| Google Workspace | Yes — Google sensitive-scope verification | days to weeks; privacy/terms pages already ready |
| GitHub | No | classic OAuth App, instant |
| Microsoft 365 | Maybe | per-customer admin consent, not a Mansur-side review |
| Slack | No | only if publicly listed in Slack App Directory (not needed) |
| Discord | No | only past 100 guilds or privileged intents |
| Zoom | No | only for public Marketplace listing |
| Box | No | Custom App, instant |
| Salesforce | No | only for AppExchange listing |
| HubSpot | No | only for App Marketplace listing |
| DocuSign | **Yes — Go-Live review** | slowest one here, start early |

---

## Uncertain items — verify yourself before relying on them

- **Microsoft Entra console URL** (`https://entra.microsoft.com/`) — current as of this writing, but
  Microsoft has renamed/relocated this admin UI more than once (Azure AD → Entra ID); confirm you land
  on "App registrations" when you get there.
- **HubSpot developer-portal create-app path** — the top-level `developers.hubspot.com` URL is stable,
  but the exact click-path to "create app" has moved around HubSpot's own UI before.
- **DocuSign Go-Live process** — flagged above; general industry knowledge, not verified against
  DocuSign's current site or this repo's code.
- **Zoom / Box scope minimums** — the platform code requests no explicit OAuth scopes for either
  (scopes are configured on the app itself, not sent in the authorize request), so the scope
  recommendations above are a reasonable judgment call, not a value pulled from code.

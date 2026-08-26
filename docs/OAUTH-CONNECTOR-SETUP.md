# OAuth Connector Setup — the 10 connectors that need a console visit

Closes MAN-360 (Slack) and MAN-361 (the rest). This is the **only** document
you need to make these ten connectors work on production (empyralis.ai).
Everything below — the env var names, the redirect URI, the scopes — was
read directly out of `server_modules/connection_oauth_service.py` and
`server_modules/routes_connections.py` on this branch, not recalled from
memory. Where a provider's own current docs didn't say something plainly
enough to state as fact, it's marked **UNVERIFIED — check**.

## Read this first: you do NOT have to register 73 apps

**62 of the 72 connectors self-register and need nothing from you.** They
declare an RFC 7591 `registration_endpoint`, and `_resolve_oauth_client()`
tries Dynamic Client Registration BEFORE it ever looks at an env var — so
they work against a completely empty environment. DCR is on by DEFAULT
(`_dynamic_registration_enabled` returns true whenever a provider declares
an endpoint and does not set `dynamic_registration_opt_in_required`).

**Notion and Linear are in that self-registering set** — `registration_endpoint`
`https://mcp.notion.com/register` and Linear's equivalent. Do not go looking
for a console for either. Same for Atlassian's own DCR endpoint.

An earlier version of this file said "73 other connectors ... gated on the
identical env-var mechanism." That was WRONG, and it was wrong in the
expensive direction — it counted `_CLIENT_ID` names in the config table and
never read `_resolve_oauth_client`, which tries DCR first. Corrected
2026-08-26 after the founder pushed back on it.

Scope, then — the ten that genuinely need a console visit:

| needs you | why it is not DCR |
|---|---|
| Slack, GitHub, Google Workspace, Microsoft 365, Salesforce, HubSpot, Box, Confluence, DocuSign, Zoom | their OAuth servers publish no `registration_endpoint`; a human must create the app |

Higgsfield is a special case and is included below: it HAS a
`registration_endpoint` but sets `dynamic_registration_opt_in_required`, so
it stays inert until one env var flips it on. No console, one variable.

## The one redirect URI, for every provider except Higgsfield

Traced from `connection_oauth_service.request_origin()` /
`callback_url()` (`connection_oauth_service.py:2314-2323`): the app builds
`{origin}/api/connections/oauth/{provider}/callback`, where `origin` comes
from the `X-Forwarded-Proto`/`X-Forwarded-Host` headers Cloudflare/nginx set
in front of production (falling back to the request's own `base_url` if
those are absent). On empyralis.ai that resolves to the literal string:

```
https://empyralis.ai/api/connections/oauth/{provider}/callback
```

Substitute the provider key for `{provider}` exactly as shown per section
below (e.g. `google_workspace`, `microsoft_365` — underscores, not
hyphens). **This is the only redirect URI to paste, for every provider on
this list.** No provider here needs a second "setup"/"install" URL beyond
it — Microsoft 365's Graph MCP entry has `endpoint: None` (a tenant-specific
preview with nothing to register against) and never needs a second URL
either.

## Higgsfield needs no console at all

Higgsfield is the one provider on this list wired for RFC 7591 Dynamic
Client Registration (`registration_endpoint` set on its config,
`connection_oauth_service.py:1232-1248`). There is no developer console for
its MCP OAuth app to register in — the app self-registers the first time a
real connect happens, and Empyralis's code caches the resulting client
id/secret in the credential vault automatically. The only thing required is
one environment variable to opt in (`dynamic_registration_opt_in_required`
defaults `True` for this provider, so it stays inert until you flip it):

```
HIGGSFIELD_OAUTH_ENABLED=true
```

That's the entire Higgsfield section. No client ID, no secret, no console
URL, ~1 minute.

## Summary table

| Provider | Env vars (production) | Review/approval needed? | Est. minutes |
|---|---|---|---|
| Slack | `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET` | No, for an internal/unlisted app | 10 |
| GitHub | `GITHUB_OAUTH_CLIENT_ID` (or `GITHUB_CLIENT_ID`), `GITHUB_OAUTH_CLIENT_SECRET` (or `GITHUB_CLIENT_SECRET`) | No for OAuth itself. **But: our MCP tool execution goes through `api.githubcopilot.com/mcp/`, which requires a GitHub Copilot / Copilot Enterprise seat on the authorizing account — flag loudly, see below** | 5 |
| Google Workspace | `GOOGLE_WORKSPACE_OAUTH_CLIENT_ID`, `GOOGLE_WORKSPACE_OAUTH_CLIENT_SECRET` | **Not for launch** (Testing mode, up to 100 test users, no review) — **YES if you want it open past 100 real users**: `drive.file` is a Google "sensitive" scope, needs Google's verification review (weeks) to leave Testing/100-user cap | 20 (Testing mode) |
| Microsoft 365 | `MICROSOFT_365_OAUTH_CLIENT_ID`, `MICROSOFT_365_OAUTH_CLIENT_SECRET` | No review from Microsoft. **Caveat**: some customer tenants disable end-user consent — that customer's own IT admin, not you, has to grant admin consent for `Mail.ReadWrite`/`Calendars.ReadWrite`/`Files.ReadWrite.All`. Nothing to do on our side; just know it can block a specific customer | 15 |
| Box | `BOX_CLIENT_ID`, `BOX_CLIENT_SECRET` | No | 10 |
| DocuSign | `DOCUSIGN_CLIENT_ID`, `DOCUSIGN_CLIENT_SECRET` | **YES — launch-blocking for real accounts.** Integration keys are created against DocuSign's demo/sandbox environment; production use requires passing DocuSign's automated "Go Live" review to promote the key. Cannot be done in an hour — see below | 15 to create the key + unknown wait for Go-Live |
| HubSpot | `HUBSPOT_CLIENT_ID`, `HUBSPOT_CLIENT_SECRET` | No, for OAuth to function against any customer who authorizes it directly. Marketplace *listing*/certification is a separate, optional thing we are not doing | 15 |
| Salesforce | `SALESFORCE_CLIENT_ID`, `SALESFORCE_CLIENT_SECRET` | No formal review, but **Salesforce Spring '26 restricts creating new classic Connected Apps** in some orgs — use the newer "External Client App" flow if Connected App isn't offered; same callback URL/scopes apply | 15 |
| Confluence (Atlassian) | `ATLASSIAN_CLIENT_ID` (or `CONFLUENCE_CLIENT_ID`), `ATLASSIAN_CLIENT_SECRET` (or `CONFLUENCE_CLIENT_SECRET`) | No. **Note the shared name**: the config accepts the ATLASSIAN_* pair first, so one Atlassian OAuth app covers this connector — do not create a second app if you already have one | 10 |
| Zoom | `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET` | No, **only if every connecting account is the founder's own Zoom account** (Local Test). **YES for any other customer** — Zoom requires marketplace publishing + review before a non-developer account can authorize the app | 10 (own account) / blocked (other accounts) |

**Bottom line on "can this be done in an hour": DocuSign cannot** (Go-Live
review has an external, unknown wait). **Zoom cannot**, the moment it needs
to work for anyone but the founder's own Zoom login. Everything else can be
fully registered inside an hour; Google Workspace works immediately in
Testing mode and only needs the multi-week review if you want it open past
100 real customers.

---

## 1. Slack

1. Go to **https://api.slack.com/apps** → **Create New App** → **From scratch**. Name it, pick your workspace.
2. Left sidebar → **OAuth & Permissions**.
3. Under **Redirect URLs**, click **Add New Redirect URL** and paste:
   ```
   https://empyralis.ai/api/connections/oauth/slack/callback
   ```
   Click **Save URLs**.
4. Same page, under **Scopes → Bot Token Scopes**, add exactly these (our code requests this literal set — `server_modules/connectors/slack_connector.py`'s `DEFAULT_SLACK_BOT_SCOPES`):
   ```
   app_mentions:read
   channels:history
   channels:read
   chat:write
   files:write
   groups:history
   groups:read
   im:history
   im:read
   im:write
   mpim:history
   reactions:read
   users:read
   ```
5. Left sidebar → **Basic Information** → **App Credentials**. Copy **Client ID** and **Client Secret**.
6. No review needed to use an app in your own workspace or one you distribute the install link to directly; only the **Slack App Directory** listing (which we are not doing) needs review.

Env vars:
```
SLACK_CLIENT_ID=<Client ID>
SLACK_CLIENT_SECRET=<Client Secret>
```

## 2. GitHub

**We need an OAuth App, not a GitHub App** — our code exchanges tokens at
`github.com/login/oauth/access_token` (classic OAuth App shape), confirmed
at `connection_oauth_service.py:255-267`.

1. Go to **https://github.com/settings/developers** → **OAuth Apps** → **New OAuth App**. (Org-owned: `https://github.com/organizations/{org}/settings/applications/new`.)
2. **Application name**: e.g. "Empyralis". **Homepage URL**: `https://empyralis.ai`.
3. **Authorization callback URL** — paste:
   ```
   https://empyralis.ai/api/connections/oauth/github/callback
   ```
4. Click **Register application**.
5. On the app's page, copy the **Client ID**; click **Generate a new client secret** and copy it immediately (shown once).
6. No review is required for the OAuth App itself.

Scopes our code requests (not something you configure in the console for a
classic OAuth App — GitHub grants whatever's requested in the authorize URL,
subject to the user's own access): `repo`, `read:user`, `user:email`.

**Loud caveat, not a console step**: our GitHub MCP tool calls run through
`api.githubcopilot.com/mcp/` (`connection_oauth_service.py:3254-3256`,
labeled "DEPRECATED... Requires GitHub Copilot or Copilot Enterprise seat"
in `connectors_actions.py`). A customer can complete this OAuth flow fine,
but if the authorizing GitHub account/org has no Copilot seat, the actual
GitHub tools will fail downstream of a successful "Connected" state. This is
worth deciding on with the founder before launch, not something the console
steps above fix.

Env vars:
```
GITHUB_OAUTH_CLIENT_ID=<Client ID>
GITHUB_OAUTH_CLIENT_SECRET=<Client Secret>
```

## 3. Google Workspace

1. Go to **https://console.cloud.google.com/** and select or create a project.
2. **APIs & Services → OAuth consent screen**. Choose **External** (unless every user is inside a Google Workspace org you own — then **Internal** skips the review question entirely). Fill in app name, support email.
3. Under **Scopes**, add:
   ```
   openid
   email
   profile
   https://www.googleapis.com/auth/drive.file
   ```
   (Our code only requests `openid`/`email`/`profile` by default —
   `drive.file` is gated behind `GOOGLE_WORKSPACE_OAUTH_ENABLED_SCOPES`
   already defaulting to `drive` in this codebase, see
   `connection_oauth_service.py:127-129`. Declare it on the consent screen
   regardless, so the scope is available the moment the code requests it.)
4. Leave **Publishing status** as **Testing** for now, and under **Test users**, add the Google accounts (yours, and any early customers) that will connect — up to 100, no review needed, works immediately.
5. **APIs & Services → Credentials → Create Credentials → OAuth client ID**. Application type: **Web application**.
6. Under **Authorized redirect URIs**, add:
   ```
   https://empyralis.ai/api/connections/oauth/google_workspace/callback
   ```
7. Click **Create**. Copy the **Client ID** and **Client Secret** shown (also downloadable as JSON, shown only once for the secret in some flows — copy both now).
8. **Enable the APIs you're using** (Drive API at minimum) under **APIs & Services → Library**, or the OAuth flow completes but calls to Drive 403.

`drive.file` is a Google-classified **sensitive scope**. In Testing mode it
works immediately for the test users you listed in step 4 — no review. To
open it to arbitrary real customers past 100, you must submit Google's
verification review (takes on the order of weeks) and move Publishing
status to **In production**. Not launch-blocking for an initial cohort under
100 real users; is blocking for a fully public self-serve signup.

Env vars:
```
GOOGLE_WORKSPACE_OAUTH_CLIENT_ID=<Client ID>
GOOGLE_WORKSPACE_OAUTH_CLIENT_SECRET=<Client Secret>
```

## 4. Microsoft 365

1. Go to **https://entra.microsoft.com** (Microsoft Entra admin center) → **Entra ID → App registrations → New registration**.
2. Name it. Under **Supported account types**, pick **Accounts in any organizational directory (multitenant)** — customers are in tenants you don't control, so single-tenant would block every external customer.
3. Under **Redirect URI**, choose platform **Web** and paste:
   ```
   https://empyralis.ai/api/connections/oauth/microsoft_365/callback
   ```
4. Click **Register**. On the **Overview** page, copy the **Application (client) ID**.
5. **Certificates & secrets → New client secret**. Copy the secret **value** immediately (hidden after you navigate away).
6. **API permissions → Add a permission → Microsoft Graph → Delegated permissions**, add exactly:
   ```
   offline_access
   User.Read
   Mail.ReadWrite
   Mail.Send
   Calendars.ReadWrite
   Files.ReadWrite.All
   ```
7. Do **not** click "Grant admin consent" for your own tenant unless you're also going to be a test user — each *external* customer's own tenant admin (or the individual user, if their tenant allows user consent) grants consent on first connect, not you.

**UNVERIFIED — check**: whether Microsoft currently requires any kind of
"Microsoft identity platform" publisher verification (a Microsoft 365
Certified Publisher badge process) before a multitenant app can be
consented to by users outside your own tenant at scale. Small-scale usage
(a handful of pilot customers) works via ordinary per-user or per-tenant
admin consent with no such badge; Microsoft's own guidance on when the
badge becomes mandatory should be re-checked directly on
learn.microsoft.com before a wide public launch.

Env vars:
```
MICROSOFT_365_OAUTH_CLIENT_ID=<Application (client) ID>
MICROSOFT_365_OAUTH_CLIENT_SECRET=<client secret value>
```

## 5. Box

1. Go to **https://app.box.com/developers/console** (sign in with a Box account; a free developer account works — https://account.box.com/signup/n/developer if you don't have one).
2. **My Apps → Create New App**.
3. Choose **Custom App**, then **User Authentication (OAuth 2.0)**. Name it.
4. On the app's **Configuration** tab, under **OAuth 2.0 Redirect URI**, paste:
   ```
   https://empyralis.ai/api/connections/oauth/box/callback
   ```
5. Under **Application Scopes**, select the scopes this deployment actually needs (our code requests no specific OAuth scope string — Box's classic OAuth grants access per the app's configured scopes, not a scope list in the authorize URL; `connection_oauth_service.py` leaves `scopes=()` for Box). Pick **Read and write all files and folders stored in Box** unless you want to scope narrower.
6. Copy **Client ID** and **Client Secret** from the same Configuration tab.
7. No approval/review required — this works immediately.

Env vars:
```
BOX_CLIENT_ID=<Client ID>
BOX_CLIENT_SECRET=<Client Secret>
```

## 6. DocuSign — cannot be finished in an hour

1. Sign in to your **DocuSign developer (demo) account** at **https://admindemo.docusign.com** (create a free demo account at https://developers.docusign.com/ if you don't have one — this is a *separate* sandbox from a real production DocuSign account).
2. **Settings → Apps and Integration Keys → ADD APP & INTEGRATION KEY**. Name it.
3. Under the app, **ADD URI** (redirect URI) and paste:
   ```
   https://empyralis.ai/api/connections/oauth/docusign/callback
   ```
4. Copy the **Integration Key** (a GUID) — this is the Client ID. Generate a **Secret Key** and copy it.
5. Scopes our code requests: `signature`, `extended` — these are requested in the authorize URL at connect time, nothing to pre-select in the console.
6. **This integration key only works against demo/sandbox DocuSign accounts until it is promoted.** To use it against a real, paying-customer DocuSign account, you must pass DocuSign's automated **Go-Live** review, reachable from the same demo account's Apps and Integration Keys page. **This has an external turnaround time DocuSign controls, not us — do not promise "an hour" for this one.** Start the Go-Live request as early as possible; everything else in this doc can be finished while it's pending.

Env vars (usable against demo accounts the moment step 4 is done; usable
against real customer accounts only after Go-Live):
```
DOCUSIGN_CLIENT_ID=<Integration Key GUID>
DOCUSIGN_CLIENT_SECRET=<Secret Key>
```

## 7. HubSpot

1. Go to **https://developers.hubspot.com/** → sign in / create a **developer account**, then **Apps → Create app**.
2. Fill in the app card info (name, logo — cosmetic only).
3. **Auth tab**: copy the **Client ID** and **Client Secret** shown there.
4. Same **Auth** tab, **Redirect URLs** section (just below the client id/secret) → add:
   ```
   https://empyralis.ai/api/connections/oauth/hubspot/callback
   ```
5. Scroll to the **Scopes** section at the bottom of the Auth tab and select:
   ```
   oauth
   crm.objects.contacts.read
   crm.objects.contacts.write
   crm.objects.companies.read
   crm.objects.deals.read
   ```
6. No review is required for OAuth to function against any customer who
   authorizes your app directly (they just click through HubSpot's own
   consent screen). HubSpot's app **certification**/Marketplace **listing**
   process is a separate, optional track for getting discovered inside
   HubSpot's App Marketplace — not required to make the connector work, and
   out of scope here.

Env vars:
```
HUBSPOT_CLIENT_ID=<Client ID>
HUBSPOT_CLIENT_SECRET=<Client Secret>
```

## 8. Salesforce

**UNVERIFIED — check at setup time**: Salesforce's Spring '26 release
reportedly restricts creating new classic Connected Apps in some orgs,
pushing new integrations toward the newer "External Client App" model. The
steps below are the classic Connected App path; if your org's Setup menu
doesn't offer "New Connected App", use **Setup → External Client App
Manager → New External Client App** instead — the Callback URL and OAuth
scope fields are equivalent, just under a different menu name. Confirm
which one your org offers before starting.

1. **Setup** (gear icon, top right) → Quick Find box → type **App Manager** → **New Connected App** (or **External Client App Manager → New External Client App**, per the note above).
2. Fill in **Connected App Name**, **API Name**, **Contact Email**.
3. Check **Enable OAuth Settings**.
4. **Callback URL** — paste:
   ```
   https://empyralis.ai/api/connections/oauth/salesforce/callback
   ```
5. **Selected OAuth Scopes** — add:
   ```
   Manage user data via APIs (api)
   Perform requests at any time (refresh_token, offline_access)
   Access the identity URL service (openid)
   ```
6. Save. **New connected apps can take up to 10 minutes to become active** — do not treat an immediate "invalid_client_id" as broken; retry after waiting.
7. **Manage Consumer Details** on the app page to reveal **Consumer Key** (Client ID) and **Consumer Secret** (Client Secret) — Salesforce requires re-verifying your identity (email code) the first time you reveal these.
8. No formal review/approval is required for a Connected App used by your own org or by customers who log in and authorize it directly.

Env vars:
```
SALESFORCE_CLIENT_ID=<Consumer Key>
SALESFORCE_CLIENT_SECRET=<Consumer Secret>
```

## 9. Zoom

1. Go to **https://marketplace.zoom.us/** → sign in → **Develop → Build App**.
2. Choose the **General App** (OAuth) type. Name it.
3. On **Basic Information**, under **OAuth Information**, paste the redirect URL:
   ```
   https://empyralis.ai/api/connections/oauth/zoom/callback
   ```
   Also add it to the **OAuth allow list** on the same page.
4. **Scopes** tab → **Add Scopes**. Our code requests no specific scope string in the authorize URL (`connection_oauth_service.py` leaves `scopes=()` for Zoom — Zoom grants whatever the app is configured with). Add at minimum a user-profile read scope so `profile_probe` (`https://api.zoom.us/v2/users/me`) succeeds.
5. **App Credentials** on Basic Information shows the **Client ID** and **Client Secret** — copy both.
6. **Review status, stated plainly**: this app works immediately, with no review, **only for the Zoom account that owns the app** (via the **Local Test** page — "Add App Now"). To let any *other* Zoom user/customer authorize it, Zoom requires submitting the app for marketplace publishing review. **That cannot be completed in an hour** — if the launch needs real customers connecting their own Zoom accounts (not just the founder's), start the review submission early and treat this like DocuSign's Go-Live: everything else in this doc can proceed while it's pending.

Env vars:
```
ZOOM_CLIENT_ID=<Client ID>
ZOOM_CLIENT_SECRET=<Client Secret>
```

## 10. Confluence (Atlassian)

Added 2026-08-26 — it was missing from the first version of this doc, found by
parsing which provider configs actually lack a `registration_endpoint`.

**Do not create a second app if you already have an Atlassian OAuth app.**
`_provider_env` resolves `ATLASSIAN_CLIENT_ID` FIRST and only falls back to
`CONFLUENCE_CLIENT_ID`, so one Atlassian 3LO app serves this connector.

1. https://developer.atlassian.com/console/myapps/ → **Create** → **OAuth 2.0
   integration**.
2. **Authorization** → **OAuth 2.0 (3LO)** → **Add** → set the callback:
   ```
   https://empyralis.ai/api/connections/oauth/confluence/callback
   ```
3. **Permissions** → add the **Confluence API**, then **Configure** the scopes
   your customers need (read/write of pages and spaces). Atlassian requires the
   scopes be selected here, not just requested in the authorize URL.
4. **Settings** → copy the **Client ID** and **Secret**.
5. **Review**: none. A 3LO app works as soon as a customer authorizes it.
   Distribution/listing on the Atlassian Marketplace is separate and optional.

**UNVERIFIED — check at setup time**: Atlassian's console has been moving
scope configuration between the Permissions and Authorization tabs; if the
layout differs from the above, the callback URL and the resulting
client id/secret are still the only two things this integration needs.

Env vars:
```
ATLASSIAN_CLIENT_ID=<Client ID>
ATLASSIAN_CLIENT_SECRET=<Secret>
```

## 11. Higgsfield

Covered above — set `HIGGSFIELD_OAUTH_ENABLED=true`. No console, no client id/secret.

---

## Deploying the env vars to production — the step most likely to silently fail

Production does **not** read `/opt/empyralis-app/.env` at runtime.
`runtime_config._should_load_dotenv()` is only true for
dev/development/local/test — production runs with
`EMPYRALIS_DEPLOY_ENV=self-hosted` (or `production`), which is deliberately
excluded (MAN-202: an earlier unscoped dotenv load once handed a worktree
the real database's connection string). **The backend's real, running
configuration lives only in pm2's saved process environment**, and
`/root/pm2-env.sh` (root-owned, mode 600) is the one authoritative snapshot
of it.

**Do not just edit `.env` and run `pm2 restart empyralis`.** That restarts
the process with its *existing* saved env — your new vars never reach it.
And do not run `pm2 restart empyralis --update-env` from a bare shell either
— that replaces the process environment with whatever your *current shell*
happens to have, which is missing `DATABASE_URL` and everything else the
backend needs, and will crash-loop production.

The correct sequence, on the production box:

```bash
# 1. Add the new OAuth env vars to BOTH files that must stay in sync:
#    /opt/empyralis-app/.env            (so the values are readable/documented)
#    /root/pm2-env.sh                    (the file pm2 actually uses)
sudo nano /root/pm2-env.sh
# append:
#   export SLACK_CLIENT_ID=...
#   export SLACK_CLIENT_SECRET=...
#   export GITHUB_OAUTH_CLIENT_ID=...
#   export GITHUB_OAUTH_CLIENT_SECRET=...
#   export GOOGLE_WORKSPACE_OAUTH_CLIENT_ID=...
#   export GOOGLE_WORKSPACE_OAUTH_CLIENT_SECRET=...
#   export MICROSOFT_365_OAUTH_CLIENT_ID=...
#   export MICROSOFT_365_OAUTH_CLIENT_SECRET=...
#   export BOX_CLIENT_ID=...
#   export BOX_CLIENT_SECRET=...
#   export DOCUSIGN_CLIENT_ID=...
#   export DOCUSIGN_CLIENT_SECRET=...
#   export HUBSPOT_CLIENT_ID=...
#   export HUBSPOT_CLIENT_SECRET=...
#   export SALESFORCE_CLIENT_ID=...
#   export SALESFORCE_CLIENT_SECRET=...
#   export ZOOM_CLIENT_ID=...
#   export ZOOM_CLIENT_SECRET=...
#   export HIGGSFIELD_OAUTH_ENABLED=true

# 2. Load it into the CURRENT shell, then restart pm2 WITH --update-env,
#    so pm2 picks up this exact environment (not your bare shell's):
set -a; source /root/pm2-env.sh; set +a
pm2 restart empyralis --update-env && pm2 save

# 3. Confirm the backend came back up clean:
pm2 logs empyralis --lines 50
# look for: "preflight: all checks passed"
```

If the box has already been migrated to the systemd unit
(`deploy/empyralis-backend.service`, per `docs/DEPLOY-RUNBOOK.md` step 7's
optional hardening path) instead of pm2, add the same `export` lines to
whatever `EnvironmentFile=`/`Environment=` that unit reads instead, then
`systemctl daemon-reload && systemctl restart empyralis-backend`. Check
which one is actually running (`pm2 list` vs `systemctl status
empyralis-backend`) before assuming either path.

## Verifying each connector from the product UI

Once the backend has restarted with the new env vars:

1. Log into empyralis.ai as an owner of a real (or disposable test) workspace.
2. Go to the agent's **Configure → Connectors → Apps** tab (the connector card grid — see CLAUDE.md's "Apps is a ROW card" entry for what the UI looks like).
3. For each of the ten, the card should now show a **Connect** button rather than the muted "unavailable" state — that flip is driven by `oauth_provider_configured()` reading the env vars you just set, so seeing a live Connect button is your first, cheap confirmation the vars landed.
4. Click **Connect** on one provider at a time. You should be redirected to that provider's own real login/consent screen (Slack's, Google's, etc.) — **not** an error page, and **not** silently back to empyralis.ai.
5. Approve on the provider's screen. You should land back on empyralis.ai with the card now showing a connected/healthy pill.
6. For a stronger check than "the card looks connected": trigger one real read action through the agent (e.g. ask it to list Slack channels, or read a Salesforce record) and confirm it returns real data rather than an auth error — a card can show "connected" from a token that later 401s on first real use, and only a real call proves the whole chain.
7. For DocuSign and Zoom specifically: step 4-6 will only work against your **own** demo/local account until Go-Live / marketplace review completes (see their sections above) — a real customer's account will refuse the authorization screen itself, not our code, until then.

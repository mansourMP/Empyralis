# OAuth Provider Setup Runbook

Built 2026-07-17 from the `verify` branch: our own code (file:line cited throughout)
plus each provider's public developer documentation (web search). **No provider
console was opened, no account was logged into, and no credential was read or
set while preparing this doc.** Every redirect URI, scope, and env var name
below is copied out of the code that reads it, not guessed.

**How to use this:** each entry below is a ~5-minute console visit — create an
app/integration, paste one redirect URI, paste back one client ID + secret.
Do them whenever you're at a keyboard; none depend on each other except where
noted. When you're done with a section, the credential goes in
`/opt/empyralis-app/.env` on the box (owned `empyralis:empyralis`, mode `600`
— same file `docs/DEPLOY-RUNBOOK.md` §1 already documents), then:

```bash
pm2 restart empyralis        # current live restart mechanism (see DEPLOY-RUNBOOK.md §2 step 7)
```

All callback/redirect URIs below assume the production domain `https://empyralis.ai`
(confirmed in `deploy/nginx-empyralis.conf:42` and `vps_provisioning_service.py:172-178`'s
`PUBLIC_API_URL` default). If you ever run this against a different host, every
`/api/connections/oauth/*` redirect URI is derived live from the request's own
origin (`connection_oauth_service.py:652-661`) — no env var controls it — so
just substitute your domain.

---

## Part A — Quick OAuth wins (code is ready; this is pure registration + paste)

### A0. Already live — no action needed

**DigitalOcean** — OAuth popup, done. `DIGITALOCEAN_CLIENT_ID` / `DIGITALOCEAN_CLIENT_SECRET`
already wired (`vps_provisioning_service.py:55-58`), redirect
`https://empyralis.ai/api/hardware/vps/oauth/digitalocean/callback`
(`vps_provisioning_service.py:60`), routes live at
`server_modules/routes_gateway.py:1617` (start) and `:1639` (callback). Listed
here only for inventory completeness — skip it.

### A1. Zero setup — nothing for you to register

**Hetzner** and **Vultr** are pasted-API-token providers, not OAuth — there is
no operator-level client id/secret anywhere in the code
(`vps_provisioning_service.py:257-291`'s `PROVIDER_CONFIGS` entries have no
`CLIENT_ID`/`CLIENT_SECRET` constants, unlike DigitalOcean/Google/AWS). Each
customer pastes their own token via `POST /api/hardware/vps/tokens`
(`routes_gateway.py:1801`) straight into the vault. **You don't need to do
anything for these to work** — they already do. The only reason to visit their
consoles is to smoke-test the flow yourself as a first "customer" would:

- Hetzner: `console.hetzner.cloud` → pick/create a project → **Security → API Tokens** → Generate API Token (Read & Write) → paste into the Hardware page's "Connect Hetzner" token field.
- Vultr: `my.vultr.com/settings/#settingsapi` → **Account → API** → Enable API access → **Account → Users** → your user → **User Access Tokens** → Add Key → paste into the Hardware page's "Connect Vultr" token field.

### A2. One console visit, one client ID/secret pair — GitHub, Notion, Linear, Dropbox

All four run through the same generic engine
(`server_modules/connection_oauth_service.py:49-520`, `OAUTH_PROVIDER_CONFIGS`)
and the same callback route: `GET /api/connections/oauth/{provider}/callback`
(`server_modules/routes_connections.py:564`). Setup starts from the dashboard's
agent detail → **Connectors** tab (`ConnectorPicker.tsx`, fed by
`GET /api/w/{workspace_id}/fleet/agent-connectors` — `routes_fleet.py:792`),
which currently shows all four as "Not configured on this deployment" because
`oauth_provider_configured()` (`connection_oauth_service.py:704-706`) finds no
client id/secret set (`routes_fleet.py:829`).

#### GitHub
1. `github.com/settings/developers` → **OAuth Apps → New OAuth App**.
2. Homepage URL: `https://empyralis.ai`. Authorization callback URL — paste exactly:
   ```
   https://empyralis.ai/api/connections/oauth/github/callback
   ```
3. Register application → copy the **Client ID** → **Generate a new client secret** → copy it (shown once).
4. Scopes are requested by our code automatically, not chosen in the GitHub UI: `repo read:user user:email` (`connection_oauth_service.py:77`).
5. Set in `.env`:
   ```
   GITHUB_OAUTH_CLIENT_ID=
   GITHUB_OAUTH_CLIENT_SECRET=
   ```
   (`connection_oauth_service.py:73-76`; `GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET` also work as fallback names).
6. Verify: restart, open an agent's Connectors tab, GitHub should read "Connect" instead of "Not configured" — click it, approve on GitHub, land back with a stored credential.
7. **Gotcha:** the code also has a second GitHub auth mode — a GitHub App with JWT-signed installation tokens (`server_modules/connectors/github_connector.py:79-198`, needs `app_id`/`installation_id`/`private_key_pem`) — but nothing in `routes_connectors.py` / `routes_connections.py` / `connectors_actions.py` currently exposes a way to reach it from the UI. Ignore it; the OAuth App above is the only reachable path today.

#### Notion
1. `notion.so/my-integrations` → **New integration** → choose **Public** integration (required to get an OAuth client id/secret at all — an internal integration only gives you a single static token, which our code doesn't use for this path).
2. Under OAuth Domain & URIs, redirect URI — paste exactly:
   ```
   https://empyralis.ai/api/connections/oauth/notion/callback
   ```
3. Save → **Configuration/Secrets tab** → copy **OAuth client ID** and **OAuth client secret**.
4. No scope picker — Notion's model is page-by-page access granted by the user during consent, not a scope list (`connection_oauth_service.py:127` scopes is deliberately empty; `auth_params={"owner": "user"}` at `:133`).
5. Set in `.env`:
   ```
   NOTION_OAUTH_CLIENT_ID=
   NOTION_OAUTH_CLIENT_SECRET=
   ```
   (`connection_oauth_service.py:124-125`).
6. Verify same as GitHub above.

#### Linear
1. Log into the Linear workspace you want to test with (must be a workspace admin) → **Settings → API** (or directly `linear.app/settings/api/applications`) → **OAuth Applications → Create new OAuth Application**.
2. Callback URLs — paste exactly:
   ```
   https://empyralis.ai/api/connections/oauth/linear/callback
   ```
3. Save → Client ID and Client Secret appear at the top of the page.
4. Scopes requested by our code: `read write` (`connection_oauth_service.py:141`).
5. Set in `.env`:
   ```
   LINEAR_OAUTH_CLIENT_ID=
   LINEAR_OAUTH_CLIENT_SECRET=
   ```
   (`connection_oauth_service.py:138-139`).
6. Verify same as GitHub above.

#### Dropbox
1. `dropbox.com/developers/apps` → **Create app** → API: **Scoped access** → Access: **Full Dropbox** → name it → Create app.
2. **Permissions tab** first — enable the scopes our code requests, or the token Dropbox issues won't actually carry them:
   `files.metadata.read`, `files.content.read`, `files.content.write`, `sharing.read`, `sharing.write` (`connection_oauth_service.py:155`) → Submit.
3. **Settings tab** → OAuth2 → Redirect URIs — paste exactly:
   ```
   https://empyralis.ai/api/connections/oauth/dropbox/callback
   ```
4. Copy **App key** and **App secret** from the top of the Settings tab.
5. Set in `.env`:
   ```
   DROPBOX_OAUTH_CLIENT_ID=
   DROPBOX_OAUTH_CLIENT_SECRET=
   ```
   (`connection_oauth_service.py:151-154`; `DROPBOX_APP_KEY`/`DROPBOX_APP_SECRET` also work as fallback names, matching Dropbox's own field labels).
6. Verify same as GitHub above.

### A3. OAuth with one extra wrinkle — Google Workspace, Microsoft 365, Google Cloud (VPS)

All three Google/Microsoft surfaces can live in one Google Cloud project /
one Entra tenant with **separate OAuth clients** (one per redirect URI below)
— you do the consent-screen setup once, then create up to 3 "OAuth client ID"
credentials under it.

#### Google Workspace (Gmail / Calendar / Drive connector)
1. `console.cloud.google.com` → pick/create a project (e.g. "Empyralis") → **APIs & Services → OAuth consent screen** → External → fill in app name/support email → **Publishing status: Testing** is fine for now (adds you as a test user, works immediately, skips Google's full verification review — see Gotcha below).
2. **APIs & Services → Credentials → Create Credentials → OAuth client ID** → Application type: **Web application**.
3. Authorized redirect URI — paste exactly:
   ```
   https://empyralis.ai/api/connections/oauth/google_workspace/callback
   ```
4. Create → copy **Client ID** and **Client secret**.
5. Scopes requested by our code: `openid email profile`, `gmail.modify`, `calendar`, `drive.file` (`connection_oauth_service.py:56-63`) — nothing to configure on Google's side beyond having them enabled for a Testing-mode app (they are by default for common scopes like these).
6. Set in `.env`:
   ```
   GOOGLE_WORKSPACE_OAUTH_CLIENT_ID=
   GOOGLE_WORKSPACE_OAUTH_CLIENT_SECRET=
   ```
   (`connection_oauth_service.py:53-54`). If dashboard "Sign in with Google" (`GOOGLE_OAUTH_CLIENT_ID`/`GOOGLE_OAUTH_CLIENT_SECRET`, `frontend/.env.example:7-8`) is already set, this connector will silently reuse it as a fallback — but only if you've also added this connector's redirect URI to *that* client's Authorized redirect URIs. Cleaner to create a dedicated client as above.
7. **Gotcha (real bug, not just advice):** `frontend/.env.example:16-18` documents a `GOOGLE_WORKSPACE_ENABLE_DRIVE_SCOPE=false` flag meant to keep the sensitive Drive scope out of the consent request until you're ready for it. It doesn't work — see Readiness Gaps below. Today Drive is always requested regardless of the flag, so budget for it in your consent-screen review either way.
8. **Gotcha:** Testing-mode apps show an "unverified app" warning screen and cap at 100 test users — fine for your own pass and early customers, but Google's verification review (required to remove the warning at scale, and mandatory for `gmail.modify`/Drive at real volume) is a separate, slower process — not needed for this 5-minute pass.

#### Microsoft 365 (Outlook / Calendar / OneDrive connector)
1. `entra.microsoft.com` (or Azure Portal → Microsoft Entra ID) → **App registrations → New registration**. Name it, leave **Supported account types** on the multi-tenant option (our code defaults the tenant to `common` if you don't set one — `connection_oauth_service.py:721-726` — so multi-tenant avoids a config step).
2. Register → copy the **Application (client) ID**.
3. **Authentication → Add a platform → Web** → redirect URI, paste exactly:
   ```
   https://empyralis.ai/api/connections/oauth/microsoft_365/callback
   ```
4. **Certificates & secrets → New client secret** → copy the **Value** immediately (shown once).
5. **API permissions → Add a permission → Microsoft Graph → Delegated permissions**, add: `offline_access`, `User.Read`, `Mail.ReadWrite`, `Mail.Send`, `Calendars.ReadWrite`, `Files.ReadWrite.All` (`connection_oauth_service.py:91-98`, matching exactly). If your tenant restricts user consent, also click **Grant admin consent**.
6. Set in `.env`:
   ```
   MICROSOFT_365_OAUTH_CLIENT_ID=
   MICROSOFT_365_OAUTH_CLIENT_SECRET=
   ```
   (`connection_oauth_service.py:88-89`).
7. **Gotcha:** OAuth will work end-to-end, but there's currently no live Microsoft MCP endpoint for tool execution to ride on — see Readiness Gaps below. Worth registering now anyway since it's a 5-minute console visit either way, but don't expect the agent to do much with it immediately after connecting.

#### Google Cloud (VPS provisioning)
This is two separate things: the per-customer OAuth client (below), and a
one-time **operator identity** only you need (Part A3.1 below it) — customers
never see or touch the operator identity.

1. Same Google Cloud project as Google Workspace above (or a new one) → **Credentials → Create Credentials → OAuth client ID** → Web application.
2. Redirect URI — paste exactly:
   ```
   https://empyralis.ai/api/hardware/vps/oauth/google/callback
   ```
   (`vps_provisioning_service.py:93-95`; override via `EMPYRALIS_GOOGLE_OAUTH_REDIRECT_URI` if you ever need a different one — `:90`).
3. Scope requested: `https://www.googleapis.com/auth/cloud-platform` (`vps_provisioning_service.py:96`), with `access_type=offline&prompt=consent` so the bootstrap flow survives a paused tab (`:493-494`).
4. Set in `.env`:
   ```
   GOOGLE_CLOUD_CLIENT_ID=
   GOOGLE_CLOUD_CLIENT_SECRET=
   ```
   (`vps_provisioning_service.py:91-92`).

**A3.1 — one-time operator identity (only you, once, not per-customer):** ongoing
Google Cloud provisioning calls (after a customer's one-time bootstrap) run as
*your* operator Google identity impersonating each customer's service account
(`vps_provisioning_service.py:63-107` docstring, `:1961-2024`). To set this up:
run the same "Sign in with Google" flow above as your *own* Google account
(`create_google_oauth_start`, same client id/secret you just created), with
`access_type=offline` — copy the resulting `refresh_token` and your account
email, then set:
```
GOOGLE_CLOUD_OPERATOR_CLIENT_EMAIL=
GOOGLE_CLOUD_OPERATOR_REFRESH_TOKEN=
```
(`vps_provisioning_service.py:106-107`, read at `:1968-1999`). Without these
two, every Google Cloud VPS call fails with "Empyralis's Google Cloud operator
identity is not configured" (`:1971-1973`, `:1981-1983`) even once a customer
has connected their own account.

### A4. Not OAuth — AWS cross-account IAM role

No console app registration at all — the "credential" is a CloudFormation
template hosted at a URL you control, plus your own AWS account id.

1. If you don't already have an AWS account for Empyralis's own operating identity, create one — this is the account whose 12-digit id becomes `EMPYRALIS_AWS_ACCOUNT_ID` below, i.e. the *only* AWS account the customer's IAM role trust policy will ever allow to assume it (`deploy/aws/empyralis-vps-role.yaml:57-63`).
2. Host `deploy/aws/empyralis-vps-role.yaml` (as-is, no edits needed — the account id is filled in live via the CloudFormation Quick-Create-Stack URL's `param_EmpyralisAccountId` query param, not baked into the file) at a public HTTPS URL — e.g. an S3 bucket with public read, or a static path under `empyralis.ai`.
3. Set in `.env`:
   ```
   EMPYRALIS_AWS_ACCOUNT_ID=
   EMPYRALIS_AWS_CFN_TEMPLATE_URL=
   ```
   (`vps_provisioning_service.py:141-142`, read at `:2636-2657`).
4. boto3 is already an installed dependency (`requirements.txt:28`, `requirements-worker.txt:12`) — nothing else to install.
5. Verify: Hardware page → Connect AWS → type any 12-digit test account id you control → you should get back a pre-filled CloudFormation Quick-Create-Stack link (`routes_gateway.py:1828`, `create_aws_connect_intent` at `vps_provisioning_service.py:2698`) → run the stack in that AWS account → **Confirm** (`routes_gateway.py:1860`, `confirm_aws_connection` at `:2739`) should succeed via `sts:AssumeRole`.

---

## Part B — Needs more (code/infra work first; registering here won't do anything yet)

### B1. MCP OAuth connector — "Add Empyralis to the Claude app"

This is fundamentally different from every provider above: **Empyralis is the
OAuth *provider* here, not a client** — there is no third-party console to
register on. A Claude/ChatGPT user pastes our MCP server's URL into their own
"Add custom connector" dialog, and our server (`server_modules/mcp_oauth_provider.py`,
implementing the MCP SDK's OAuth Authorization Server interface with dynamic
client registration per RFC 7591 and discovery per RFC 8414/9728) handles the
rest — no per-client id/secret to hand out manually.

**Why it's "needs more," not a runbook entry:**

1. **The code isn't on `verify` (or `main`) yet.** `server_modules/mcp_oauth_provider.py`
   and its wiring in `mcp_server.py` exist only on branch `fix/mcp-oauth-connector`
   (single commit `6da1dee78`, 1276 lines + a 791-line test file, currently 10
   commits behind `verify` on unrelated work — needs a rebase/merge, not a
   rewrite). Confirmed absent from `verify` and `main` via `git show <branch>:server_modules/mcp_oauth_provider.py`.
2. **nginx on the live box doesn't route any of the paths this needs**, even
   after merging. Compare `deploy/nginx-empyralis.conf` on `verify` (only
   `/api/v1/`, `/api/gateway/`, `/health`, and a catch-all `/` to Next.js —
   lines 52-102) against the same file on `fix/mcp-oauth-connector`, which adds
   dedicated `location` blocks for `/mcp`, `/mcp/consent`,
   `/authorize|/token|/register|/revoke`, and
   `/.well-known/oauth-authorization-server` /
   `/.well-known/oauth-protected-resource/mcp` — that branch's own comment
   states plainly: *"Before this, NONE of those paths had a location block, so
   they fell through to the `location /` catch-all and were served (404'd) by
   Next.js."* Merging the Python code alone does not fix this — the nginx
   config on the box needs the same update, deployed the same way
   `DEPLOY-RUNBOOK.md` §2 step 6 already documents for other nginx changes.
3. Once both of the above land, activation is two env vars, no external
   registration:
   ```
   EMPYRALIS_MCP_OAUTH_ENABLED=true
   EMPYRALIS_PUBLIC_BASE_URL=https://empyralis.ai
   ```
   (`mcp_server.py:93-112`; without a resolvable `EMPYRALIS_PUBLIC_BASE_URL`,
   `_build_mcp_server()` logs a warning and silently falls back to the
   legacy bearer-key-only path — `:218-224`). The URL a user pastes into
   Claude's "Add custom connector" dialog would then be
   `https://empyralis.ai/mcp` (`EMPYRALIST_MCP_PATH = "/mcp"`, `mcp_server.py:68`).
   Optional: `EMPYRALIS_MCP_WRITE_ENABLED=true` additionally gates whether
   write-capable tools (create agent, send message, etc.) are available at all,
   independent of OAuth (`mcp_server.py:89-91`).

Nothing to register anywhere for this one — it's a merge-and-deploy task, not
a credentials task. Revisit once items 1 and 2 above are done.

---

## Readiness table

"Ready-for-creds" = callback handler exists, scopes are wired correctly, env
vars are read and plumbed through — pasting a real client id/secret today
would make it work with no further code changes. Verified by reading the
actual code paths, not by testing (no credentials were set).

| Provider | Auth model | Readiness | Notes |
|---|---|---|---|
| DigitalOcean | OAuth popup | **ready-for-creds** (already live) | done |
| Hetzner | Token paste | **ready-for-creds** | no operator credential exists or is needed |
| Vultr | Token paste | **ready-for-creds** | no operator credential exists or is needed |
| Google Cloud (VPS) | OAuth + operator bootstrap | **ready-for-creds** | needs your client id/secret + one-time operator refresh token (ops, not code) |
| AWS | Cross-account IAM role (CloudFormation) | **ready-for-creds** | needs your AWS account id + template hosted at a URL (ops, not code); boto3 already installed |
| GitHub (connector) | OAuth popup | **ready-for-creds** | see gotchas below (non-blocking) |
| Notion (connector) | OAuth popup | **ready-for-creds** | — |
| Linear (connector) | OAuth popup | **ready-for-creds** | — |
| Dropbox (connector) | OAuth popup | **ready-for-creds** | — |
| Microsoft 365 (connector) | OAuth popup | **ready-for-creds** | OAuth itself is fully wired; downstream tool execution is not (see gaps) |
| Google Workspace (connector) | OAuth popup | **needs-small-wiring** | scope-gating bug, see gaps below |
| MCP OAuth connector ("Add to Claude") | Self-hosted OAuth 2.1 authorization server (no external console) | **needs-substantial-code** | unmerged branch + missing nginx routes |

---

## Top gaps that need code/ops work before creds fully pay off

Ordered by what blocks the most value:

1. **MCP OAuth connector not merged + nginx not wired.**
   `server_modules/mcp_oauth_provider.py` and its `mcp_server.py` wiring exist
   only on `fix/mcp-oauth-connector` (not `verify`/`main`); `deploy/nginx-empyralis.conf`
   on `verify` has no location blocks for `/mcp`, `/mcp/consent`,
   `/authorize`, `/token`, `/register`, `/revoke`, or the two
   `/.well-known/oauth-*` discovery paths (compare `verify`'s
   `deploy/nginx-empyralis.conf:41-109` against the same file on
   `fix/mcp-oauth-connector`). Two deploy actions, no new code needed — the
   feature branch already has a 791-line test file
   (`server_modules/tests/test_mcp_oauth_provider.py`). **This is the single
   biggest lever**: it's the only item in this doc that unlocks an entirely
   new distribution surface (Claude/ChatGPT users adding Empyralis as a
   connector) rather than one more app integration.

2. **Google Workspace's Drive-scope opt-out flag is inert.**
   `connection_oauth_service.py:56-63` bakes `drive.file` into
   `google_workspace`'s base scope tuple unconditionally. The gating logic at
   `:736-752` (`_effective_scopes`) only ever *adds* `drive.file` when
   `GOOGLE_WORKSPACE_ENABLE_DRIVE_SCOPE=true` — via
   `if drive_scope not in scopes: scopes.append(...)` — but since the base
   tuple already contains it, that check is always false and the append never
   fires either way. Net effect: Drive is requested on every Google Workspace
   consent screen regardless of the flag, contradicting the stated intent at
   `frontend/.env.example:16-18` ("Keep Drive out of the default Google
   verification pass unless Drive is also demo-ready"). One-line fix: either
   remove `drive.file` from the base tuple and let the existing conditional
   add it back, or delete the dead flag and accept Drive is always requested.

3. **Microsoft 365 has no live MCP tool-execution endpoint.**
   `connection_oauth_service.py:1236-1238` (`APP_MCP_SERVER_MAP["microsoft_365"]`)
   sets `endpoint: None` with the comment "No single public endpoint yet...
   Frontier preview... Checked 2026-06-28 — watch for GA announcement." Per
   the map's own contract comment at `:1193`, `endpoint: None` means "credential
   stored but no tools available." The one surviving legacy custom action is
   `browse_drive` (`connectors_actions.py:146-155`); every other Microsoft 365
   action was cleared when the codebase moved to the MCP-pipeline model. A
   customer can complete OAuth today, but the agent can do very little with
   it until Microsoft ships a public endpoint (external dependency, not
   something to build now) or a broader custom fallback gets built.

4. **GitHub's MCP tool execution depends on the connected account having a
   Copilot seat.** `connectors_actions.py:156-157`: GitHub's custom tool
   actions were deprecated in favor of GitHub's own official remote MCP server
   at `https://api.githubcopilot.com/mcp/`
   (`connection_oauth_service.py:1212-1216`), which per GitHub's own docs
   requires a Copilot Business/Enterprise seat on the connected account/org.
   A customer without Copilot can still complete the OAuth popup (credential
   is stored fine) but the MCP tool layer likely won't authorize. Worth a
   plain-language warning in the connector's UI copy before this ships wider.

5. **GitHub App (JWT/installation) auth mode is dead code.**
   `server_modules/connectors/github_connector.py:79-198` implements a second
   GitHub auth mode (`app_id` + `installation_id` + `private_key_pem`, JWT-signed
   per-installation tokens, also handled in `validate_github_credentials` at
   `:251-259`) that has no route anywhere exposing it —
   `routes_connectors.py`, `routes_connections.py`, and `connectors_actions.py`
   never surface `auth_mode=app` as a reachable path. Not blocking (the OAuth
   App path fully covers today's need) — flagging so nobody assumes GitHub
   App installs already work end-to-end.

6. **Google/Microsoft/GitHub/Notion/Linear/Dropbox tool execution runs through
   a separate "MCP pipeline" layer this audit did not verify at runtime.**
   `connectors_actions.py:135-202` shows all six connectors' custom
   request-handling actions were deliberately cleared (`"actions": {}`) in
   favor of routing through the officially-hosted MCP servers listed in
   `connection_oauth_service.py:1191-1244` (`APP_MCP_SERVER_MAP`) — e.g.
   Google's `gmailmcp.googleapis.com`/`calendarmcp.googleapis.com`/`drivemcp.googleapis.com`,
   `mcp.notion.com/mcp`, `mcp.linear.app/mcp`, `mcp.dropbox.com/mcp`. This
   audit confirmed the *credential-collection* path (OAuth popup → stored
   token) is solid for all six; it did not verify that
   `mcp_registry_service.py`'s auto-registration actually reaches each of
   these live endpoints and discovers usable tools post-connect — that's the
   natural next verification pass once real creds are in and a real OAuth
   round-trip can be exercised (out of scope for this doc's zero-credential
   constraint). The codebase already self-reports a per-provider `live` /
   `partial` / `preview` status for exactly this at
   `GET /api/connections/mcp-catalog` (`routes_connections.py:794-858`) — worth
   checking that endpoint first before manually re-deriving it.

---

## Where each piece of UI lives (for the "verify it worked" step)

- **VPS providers** (DigitalOcean/Hetzner/Vultr/Google Cloud/AWS): dashboard
  → workspace → **Hardware** page (`frontend/app/(account)/w/[workspaceId]/hardware/page.tsx`,
  `frontend/lib/workspace/cloud-vps-setup-panel.tsx`).
- **App connectors** (Google Workspace/Microsoft 365/GitHub/Notion/Linear/Dropbox):
  dashboard → an agent's detail view → **Connectors** tab
  (`frontend/lib/workspace/fleet/ConnectorPicker.tsx`, backed by
  `GET /api/w/{workspace_id}/fleet/agent-connectors` — `routes_fleet.py:792`).
- **MCP OAuth connector**: once B1 lands, this is entirely on the *client*
  side (Claude.ai → Settings → Connectors → Add custom connector →
  `https://empyralis.ai/mcp`) — nothing in our own dashboard to click.

# Deploy Runbook — empyralis.ai

**Reality, not aspiration.** Production is a single DigitalOcean VPS (`165.227.25.201`, Ubuntu 24.04, droplet `ubuntu-s-1vcpu-2gb-sfo2`), not Render and not Docker — `render.yaml` and the app-level Dockerfiles were deleted 2026-07-10 because neither was actually in use. One git checkout at `/opt/empyralis-app` holds both the backend (repo root) and frontend (`frontend/` inside it). nginx terminates TLS (Certbot) and reverse-proxies to two local processes: FastAPI/uvicorn on `:8001`, Next.js on `:3000`. Postgres runs locally on the box. Redis does **not** run today (see Known Gaps). The Rust runtime kernel (`empyralis-runtime-kernel`, gates scheduling/governance decisions) is a **compiled binary invoked over subprocess** at `/opt/empyralis-app/empyralis-runtime-kernel/target/release/empyralis-runtime-kernel` (`EMPYRALIS_RUNTIME_KERNEL_BIN` set in `.env`) — it does **not** get rebuilt by pulling new code, only by explicitly running `cargo build` (see step 3a below). It was confirmed present 2026-07-10, but "present" is not "current": a source fix to `empyralis-runtime-kernel/src/*.rs` that lands on `main` and reaches the box via the deploy steps below changes nothing about the running binary until step 3a runs. This is exactly what happened to MAN-306 — a `runtime_state_store.rs` fix shipped 2026-07-28 with no accompanying rebuild, so the box kept enforcing the old policy for weeks with no error anywhere. `server_modules/preflight.py`'s boot check now catches this (refuses to boot if any file under `empyralis-runtime-kernel/src/` is newer than the binary), so a skipped step 3a now fails loudly on restart instead of silently. (There are also two apparent leftovers, `/opt/empyralis.OLD` and `/root/empyralis`, not investigated this pass — check before assuming either is safe to delete.)

This doc was written after directly inspecting the live box over SSH. If the box's layout has changed since 2026-07-10, verify before trusting a step here blindly.

## 1. Environment variables

Set these in `/opt/empyralis-app/.env` (owned `empyralis:empyralis`, mode `600`). Names and *shapes* below; real secret values live only in that file and in your password manager, never in git.

### ⚠️ Required BEFORE `EMPYRALIS_DEPLOY_ENV=production` — the box will refuse to boot without them

Confirmed by directly test-booting the app with the real production-style flags (no skip flags, local Postgres + Redis + kernel binary) — this is not a theoretical list, every one of these is a hard `RuntimeError` at import time once the app resolves itself as production, discovered by actually hitting each one in turn. **All five are currently unset on the live VPS.** Setting `EMPYRALIS_DEPLOY_ENV=production` without first setting all five will crash-loop the backend on the next restart — set these first, restart once, confirm `preflight: all checks passed` in the log, only then consider it done:

| Var | Value | Why |
|---|---|---|
| `ORION_JWT_SECRET` | the value already at `/root/.empyralis/state/auth/jwt_secret` on the box (64 hex chars) | **Use the existing value, don't generate a new one** — the backend currently signs sessions with this auto-generated, file-persisted secret (since nothing explicit is set); a fresh value here invalidates every live session immediately. `cat /root/.empyralis/state/auth/jwt_secret` and copy it in. |
| `EMPYRALIS_SECRETS_BROKER_SECRET` | new random 32+ char secret (`python3 -c "import secrets; print(secrets.token_hex(32))"`) | Currently entirely unset — nothing has a value to preserve, generate fresh. **Side effect worth knowing:** this being unset is *also* why BYOK (bring-your-own-API-key) credential storage almost certainly returns "Internal server error" for any real user who's tried it — `secrets_broker.py`'s `_signing_secret()` refuses to run without this regardless of environment. Setting it fixes that too. |
| `EMPYRALIS_TOOL_BROKER_SECRET` | new random 32+ char secret | Same situation as above, separate secret. |
| `EMPYRALIS_MINI_APP_SHARE_SECRET` | new random 32+ char secret | Currently entirely unset, nothing to preserve. |
| `EMPYRALIS_PUBLIC_API_URL` | leave **unset**, or if set explicitly, `https://empyralis.ai/api` (**must include `/api`**) | Must be a real HTTPS URL, not loopback — this is a *different* concept from the frontend's `EMPYRALIS_API_URL=http://127.0.0.1:8001` (that one's the internal same-box hop; this one's "what's the publicly reachable API URL," used for links/webhooks/callbacks external services need, including the URL a freshly provisioned Agent Computer box registers to). `vps_provisioning_service.py`'s own default (used when this is unset) already correctly includes `/api`, and now also defensively appends `/api` if you set this explicitly without it — but the gateway's own `normalizeBaseUrl()` on the box side does *not* do that (it only strips a trailing slash), so don't rely on the backend's safety net: set this right, full stop. Getting it wrong sends every newly provisioned box to `{url}/gateway/registrations` with no `/api` prefix, which nginx routes to Next.js instead of the backend → 404 → the box never pairs. |

### Must be set correctly for production to be safe

| Var | Value | Why |
|---|---|---|
| `EMPYRALIS_DEPLOY_ENV` | `production` | **Currently unset on the live box** (only `ORION_ENV=self-hosted` is set, which most of the codebase's env-resolution doesn't recognize as production). This one var is checked first everywhere and fixes the largest number of gaps at once: durable-runtime-state enforcement, the `FRONTEND_ORIGINS` boot-time safety assertion, and (combined with the nginx fix in `deploy/nginx-empyralis.conf`) cookie `Secure`. Leave `ORION_ENV=self-hosted` in place too — nothing currently depends on removing it, and some self-hosted-worker tooling may still read it. Requires the five vars above to already be set, or the backend won't boot — see above. |
| `FRONTEND_ORIGINS` | `https://empyralis.ai` | Drives the actual browser CORS allowlist (`CORSMiddleware`). Empty/wildcard is refused at boot once `EMPYRALIS_DEPLOY_ENV=production` is set — verify the box actually restarts cleanly after setting both. |
| `CONTROL_PLANE_ORIGINS` | `https://empyralis.ai` | A *separate* Origin-header allowlist (mutating requests only). Defaults to `FRONTEND_ORIGINS` if unset — setting both explicitly avoids relying on that default silently doing the right thing. |
| `EMPYRALIS_AUTH_COOKIE_SAMESITE` | `lax` | Correct for this same-origin (frontend+backend under one domain) topology — set it explicitly rather than relying on the production-resolution default landing on the right value by accident. |
| `EMPYRALIS_SKIP_RLS_CHECK` | **unset** | Confirmed already unset on the live box — keep it that way. Setting it bypasses real tenant-isolation verification. |
| `EMPYRALIS_SKIP_REDIS_CHECK` | **unset it** | **Currently set to `true` on the live box.** Redis isn't running there either — see Known Gaps below for the actual decision to make before unsetting this. |
| `ORION_DEV_INSECURE_NO_AUTH` | **unset** | Confirmed already unset — keep it that way. This one is self-defending (refuses to boot outside `ORION_ENV=local|test`), but don't rely on that. |
| `EMPYRALIS_DEV_ALLOW_HTTP_MCP` | **unset** | Confirmed already unset — keep it that way. |

### Feature flags

| Var | Value | Effect |
|---|---|---|
| `EMPYRALIS_INVITE_CODE` | unset, or a real code | Unset = open signup (today's behavior, unchanged). Set = signup requires this exact code; wrong/missing code gets an honest "Empyralis is invite-only right now." Change this whenever you want to open or close signups — no redeploy needed, just edit `.env` and restart the backend. |
| `EMPYRALIS_TOOL_HONESTY_GUARD_ENABLED` | unset (defaults to on) or `1` | Structural guard that blocks a reply from shipping if it contradicts the turn's real tool trace. Leave on in production; `0` is the empirical-testing escape hatch, not a prod toggle. |
| `EMPYRALIS_FORCE_LEGACY_ENGINE` | unset (defaults off) or `1` | MAN-312 emergency rollback: the Claude Agent SDK is the default turn engine for every agent (`sage_agent_runtime_service._resolve_turn_engine_id`) — set this to `1` and restart the backend to force EVERY turn on EVERY agent/workspace back onto the legacy engine (`direct_chat_generation_service`) with no redeploy, overriding any per-agent `model_config.engine` choice. Whole-fleet only, not per-tenant — for reverting a single misbehaving agent instead, set that agent's `model_config.engine` to `"legacy"` via `fleet_configure_agent` (no dedicated frontend UI for this yet; reachable via the fleet API or by asking an operator's own agent to make the change). Unset (or `0`) to go back to the SDK default. |

### Provider keys, secrets (values are real credentials — never commit)

`ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `OPENAI_API_KEY` / others as configured providers require it · `DATABASE_URL` (local Postgres) · `ORION_JWT_SECRET` (persisted at `~/.empyralis/state/auth/jwt_secret` — do not rotate casually, it invalidates every live session) · `CREDENTIAL_VAULT_KEY` · `EMPYRALIS_SECRETS_BROKER_SECRET` / `EMPYRALIS_TOOL_BROKER_SECRET` (BYOK credential storage refuses to start without these) · `EMPYRALIS_TELEGRAM_HOSTED_BOT_TOKEN` (confirmed this session: never committed to git, lives only in this file).

Third-party OAuth connector client_id/secret pairs (`GOOGLE_WORKSPACE_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_ID`, etc.) are a separate, larger checklist — see `docs/OAUTH-PROVIDER-SETUP.md` for which of the 26 connector providers need one of these registered manually vs. self-register with no setup.

### Frontend (`/opt/empyralis-app/frontend`, or via `deploy/empyralis-frontend.service`'s `Environment=` lines)

| Var | Value |
|---|---|
| `EMPYRALIS_API_URL` | `http://127.0.0.1:8001` — same-box loopback is correct and code-supported (explicit localhost exemption from the HTTPS-required check), do not point this at the public domain. |
| `NODE_ENV` | `production` |

### Agent Computer provisioning (required for the "Connect a cloud server" flow)

**Which providers are actually reachable, and why two of them 500 today.** All
three provider code paths (DigitalOcean, Google Cloud, AWS) are complete,
unit-tested and wired from UI click to the real cloud API — `provision_vps()`
in `vps_provisioning_service.py` dispatches to `_provision_digitalocean` /
`_provision_google` / `_provision_aws`, and `cloud_init_script()` is
provider-agnostic (one `#cloud-config` wrapping `install-agent-computer.sh`,
no per-provider branches). What was missing until this section existed is
purely OPERATOR configuration, and because it was never written down, two
finished providers sat unusable and invisible for weeks. That is the same
"built, tested, and never wired" shape CLAUDE.md names, one level up from the
code: the code was wired, the deploy checklist was not.

Verified on the live box 2026-08-12: DigitalOcean set, Google Cloud and AWS
entirely unset. Both unset providers fail LOUDLY (HTTP 500 from
`create_google_oauth_start` / `empyralis_aws_account_id()` raising), never
silently, so no customer money is at risk — but the feature is dead end to end.

| Var | Provider | Why |
|---|---|---|
| `GOOGLE_CLOUD_CLIENT_ID` / `GOOGLE_CLOUD_CLIENT_SECRET` | Google Cloud | A SECOND OAuth app, deliberately NOT the sign-in one. It requests `cloud-platform`, which Google classifies **sensitive** — putting it on the sign-in consent screen would drag Google Sign-In back under verification and re-impose the 100-user cap that was removed on 2026-08-12 by stripping `calendar`/`gmail.modify`. Keep the two consent screens separate, permanently. |
| `GOOGLE_CLOUD_OPERATOR_CLIENT_EMAIL` / `GOOGLE_CLOUD_OPERATOR_REFRESH_TOKEN` | Google Cloud | Empyralis's OWN long-lived GCP identity. Every customer's bootstrap grants THIS identity `roles/iam.serviceAccountTokenCreator` on the `empyralis-provisioner` service account created inside the customer's own project; every later call impersonates that SA via `iamcredentials:generateAccessToken`, and the customer's OAuth token is discarded immediately after bootstrap. **Minting the refresh token requires a human to run a one-time OAuth consent as Empyralis itself** — it cannot be generated from a key file or by any automation here. |
| `EMPYRALIS_AWS_ACCOUNT_ID` | AWS | The 12-digit id of Empyralis's own AWS account. Not a secret — it is published INTO every customer's IAM trust policy so `sts:AssumeRole` works. See CLAUDE.md: AWS is deliberately deferred until the founder can create this account on hardware and a phone he owns, because losing it later breaks every customer at once. |
| `EMPYRALIS_AWS_CFN_TEMPLATE_URL` | AWS | A publicly fetchable copy of `deploy/aws/empyralis-vps-role.yaml` (e.g. S3). Customers run it as a CloudFormation stack to create the `EmpyralisVPSProvisioner` role. There is **no publish pipeline** for this file — unlike the DigitalOcean baked image, nothing uploads it, so it is a manual step and will silently go stale if the YAML changes. |
| `DIGITALOCEAN_CLIENT_ID` / `DIGITALOCEAN_CLIENT_SECRET` | DigitalOcean | Already set. Only powers the OAuth *button*; the paste-a-token path works without them. |

Baked images (Packer) are **DigitalOcean-only** — `deploy/packer/agent-computer.pkr.hcl`
declares a single `source "digitalocean"`. Google and AWS always take the full
boot-time install, which is slower but functionally identical and uses the same
failure-beacon plumbing.


| Var | Value | Why |
|---|---|---|
| `EMPYRALIS_REPO_TOKEN` | a GitHub personal access token with **read** access to `mansourMP/Empyralis` | The Empyralis repo is private and no build-artifact publish pipeline exists yet, so `install-agent-computer.sh` clones the repo directly on a freshly provisioned box instead of downloading a prebuilt tarball. `vps_provisioning_service.py`'s `cloud_init_script()` reads this once from the backend's own environment (`os.getenv`, same pattern as `DIGITALOCEAN_CLIENT_SECRET`) and threads it into the cloud-init command every newly created Hetzner/Vultr/DigitalOcean box runs at first boot. **Without it, a freshly provisioned box can't clone the repo, can't build the gateway, and never pairs** — provisioning fails silently at the box's first-boot step, not at backend boot, so it won't show up in this box's own logs; check the new box's `cloud-init-output.log` instead. Generate at github.com → Settings → Developer settings → Personal access tokens (read-only; scope to just this repo if using a fine-grained token). Interim measure while the repo is private and unpublished — see `vps_provisioning_service.py`'s own comment on `_installer_repo_token()` for the longer-term options (a public gateway mirror, or a real CI publish pipeline). |

## 2. Deploy steps, in order

1. **Pre-flight, once per box (skip if already done — check `git log -1` on both):**
   ```bash
   ssh root@165.227.25.201
   cd /opt/empyralis-app
   git log -1 --oneline   # compare against your local HEAD before pulling
   ```
2. **Pull code.** `git pull` does **not** work directly on the box — confirmed 2026-07-10, there's no stored GitHub credential (`~/.git-credentials`, a credential helper, an SSH deploy key) and never has been. Push directly over the SSH access you already have instead, via a throwaway local bare mirror (no GitHub auth needed, since it's all filesystem/SSH):
   ```bash
   # from your local machine, one time (skip if the mirror already exists):
   ssh root@165.227.25.201 "mkdir -p /opt/empyralis-deploy-mirror.git && cd /opt/empyralis-deploy-mirror.git && git init --bare"
   # from your local machine, each deploy:
   git push ssh://root@165.227.25.201/opt/empyralis-deploy-mirror.git verify:verify
   # on the box:
   cd /opt/empyralis-app
   git fetch /opt/empyralis-deploy-mirror.git verify:refs/deploy-incoming
   git merge --ff-only refs/deploy-incoming   # fails loudly if it's not a clean fast-forward — don't force past that, investigate first
   git update-ref -d refs/deploy-incoming     # tidy up the temp ref
   ```
   If a real GitHub credential ever gets provisioned on the box, `git fetch origin && git checkout verify && git pull` becomes the simpler path — until then, use the mirror.
3. **Backend deps:**
   ```bash
   .venv/bin/pip install -r requirements.txt -r requirements-worker.txt
   ```
3a. **Rust runtime kernel — rebuild whenever `empyralis-runtime-kernel/` changed** (check with `git diff --stat <previous-deployed-sha> HEAD -- empyralis-runtime-kernel/`; when in doubt, just run it — a no-op rebuild is cheap and the boot-time staleness check below will refuse to start the backend if you skip a needed one):
   ```bash
   cargo build --release --manifest-path empyralis-runtime-kernel/Cargo.toml
   ```
   This step is easy to forget because nothing else in this checklist touches it — that's exactly how MAN-306 happened (a `runtime_state_store.rs` fix shipped 2026-07-28 and reached the box via steps 1-2, but the binary was never rebuilt, so it kept enforcing the pre-fix policy and blocked every ordinary completed task run's archive write for weeks with no error anywhere). `server_modules/preflight.py`'s boot check now compares the binary's mtime against every file under `empyralis-runtime-kernel/src/`, `Cargo.toml`, and `Cargo.lock`, and refuses to boot if the binary is older — so skipping this step now fails the restart in step 7 loudly instead of degrading silently.
3b. **Database migrations — apply any new file under `migrations/` BEFORE the restart in step 7** (check with `git diff --stat <previous-deployed-sha> HEAD -- migrations/`). Until 2026-08-08 this checklist had no migration step at all, which is why it is spelled out here rather than assumed.
   ```bash
   # As the APP's own role, never the postgres superuser — a superuser-applied
   # migration leaves the object owned by `postgres`, the app cannot alter its
   # own table on boot, and it crash-loops (~4 min of production downtime,
   # 2026-08-07). If you slip: ALTER TABLE <t> OWNER TO empyralis_app;
   psql "$DATABASE_URL" -f migrations/<new_file>.sql

   # After ADDING a table, re-run the RLS policy migration — a table created
   # without its policy exists with no isolation and reads return nothing:
   psql "$DATABASE_URL" -f migrations/enable_rls.sql
   ```
   **Order is load-bearing in both directions.** A migration that ADDS a table must run before the code that reads it. A migration that DROPS one must ALSO run before the code that stops excusing it: `preflight._check_rls()` asks the live database which tables carry `tenant_id`/`workspace_id` and requires each to be listed in `enable_rls.sql` or in `preflight._RLS_COVERAGE_EXCEPTIONS`. So a table that was dropped in code — its exception entry removed — but still present on the box reads as NEW un-excused drift and **fails the boot**. `migrations/drop_knowledge_rag_tables.sql` (2026-08-08, the embeddings/RAG removal) is the first instance of this shape: apply it before deploying that code.
4. **Frontend build:**
   ```bash
   cd frontend && npm ci && npm run build && cd ..
   ```
5. **Update `.env`.** On a routine deploy after the box is already hardened, this step is a no-op — skip it. The **first time**, do it as its own deliberate pass, separate from a routine code deploy: add the five prerequisite secrets from Section 1 first, restart the backend once, confirm `preflight: all checks passed` in the log (`journalctl -u empyralis-backend -n 50` or `pm2 logs empyralis --lines 50`, whichever the box is currently running), *then* add `EMPYRALIS_DEPLOY_ENV=production` and the rest of Section 1's "must be set correctly" table, and restart again. Doing this out of order — setting `EMPYRALIS_DEPLOY_ENV=production` before the five prerequisites exist — crash-loops the backend.
6. **nginx** — replace both files, don't just add:
   ```bash
   cp deploy/nginx-empyralis.conf /etc/nginx/sites-available/empyralis
   rm -f /etc/nginx/sites-enabled/empyralis.pre-mcp   # stale duplicate, causes "conflicting server name" warnings
   ln -sf /etc/nginx/sites-available/empyralis /etc/nginx/sites-enabled/empyralis
   nginx -t && systemctl reload nginx
   ```
7. **Restart the backend.** Today this is `pm2 restart empyralis` (root-owned pm2 process — that's how it currently runs, not yet migrated to systemd). Migrating to `deploy/empyralis-backend.service` (runs as the `empyralis` user instead of root, adds `--proxy-headers`) is recommended but is a real behavior change to a live process — do it as its own deliberate step, not blended into a routine deploy:
   **NEVER pass `--update-env` from a bare shell.** pm2 replaces the process
   environment with the invoking shell's, and the backend's real configuration
   lives ONLY in pm2's saved env — `/opt/empyralis-app/.env` is NOT loaded in
   production (`runtime_config._should_load_dotenv()` is false for
   `EMPYRALIS_DEPLOY_ENV=self-hosted`, deliberately, per MAN-202). A bare
   `--update-env` therefore silently strips DATABASE_URL and every other
   runtime secret. `/root/pm2-env.sh` (root, 0600) is the authoritative
   snapshot; source it whenever env must change.

   ```bash
   # routine deploy, current mechanism (no env change):
   pm2 restart empyralis
   # ONLY when environment variables must change:
   set -a; source /root/pm2-env.sh; set +a
   pm2 restart empyralis --update-env && pm2 save
   # OR, the one-time hardening migration:
   pm2 delete empyralis
   cp deploy/empyralis-backend.service /etc/systemd/system/
   systemctl daemon-reload && systemctl enable --now empyralis-backend
   ```
8. **Restart the frontend:**
   ```bash
   systemctl restart empyralis-frontend
   ```
9. **Clean up the orphaned "v2" leftovers** (found during the 2026-07-10 audit, safe to remove — confirmed not receiving any traffic): the `/opt/empyralis-new-frontend` directory and its orphaned `next-server` process (find via `ss -tlnp | grep 3001`, it's not referenced by nginx), and the two inactive, wrong-layout systemd units `empyralis-api.service` / `empyralis-bot.service` (`systemctl disable --now empyralis-api empyralis-bot 2>/dev/null; rm -f /etc/systemd/system/empyralis-{api,bot}.service`).

## 3. Post-deploy smoke test

Run against `https://empyralis.ai`, not the IP, so cookies/CORS get exercised for real:

1. `curl -I https://empyralis.ai/health` → `200`.
2. Visit `https://empyralis.ai/` logged out → redirects to `/login` (not the old marketing page — it's deleted).
3. Sign up a fresh account (with the invite code if `EMPYRALIS_INVITE_CODE` is set) → lands in a new workspace, not a 404 or blank screen.
4. Open browser devtools → Application → Cookies → confirm `empyralis_access_token`/`empyralis_refresh_token` show `Secure` ✓ and `HttpOnly` ✓ (this is the cookie gap fixed in this pass — actually check it, don't assume).
5. Run the create-agent wizard end to end → agent appears in the fleet list.
6. Send that agent one message → reply arrives, and `Billing`/usage shows a nonzero cost for it (confirms metering, not just that the LLM call succeeded).
7. Stop the agent, confirm the stop shows up in the Inbox with the right attribution; resume it, confirm resume shows up too.
8. Log out, then log back in with the same account → session round-trips correctly (validates cookie `SameSite`/`Domain` didn't break normal auth).
9. Open the site on a phone (real device or devtools mobile emulation) → layout doesn't break, login works, chat is usable.
10. `journalctl -u empyralis-frontend -u empyralis-backend --since "10 minutes ago" | grep -i error` → nothing unexpected (a few pre-existing Server-Action-ID-mismatch lines from stale browser tabs are known and harmless, see Known Gaps).

## 3a. Repairing a gateway that cannot receive updates (MAN-355)

**Symptom, and the only place it shows:** the Hardware page's Gateway version
row says **"Can't receive updates"** instead of "Up to date", with a reason and
a command block underneath it. That is the box telling you its own supervisor
unit starts the gateway from a fixed path that updates are never installed
into, so a self-update would download, stage, swap — and then start the old
copy again.

**Why the gateway cannot fix this itself** (measured on production
2026-08-18, read-only — three independent barriers, any one of them fatal):

```
Uid 995 (empyralis-gw)   /etc/systemd/system is root:root 0755
                         `sudo -u empyralis-gw test -w` → NOT WRITABLE
ProtectSystem=strict     `/` is mounted `ro` inside the unit's own mount
                         namespace — even root inside it cannot write there
NoNewPrivileges=true     no setuid, no sudo, no escalation path
```

plus `systemctl daemon-reload` needs root or a polkit rule the box does not
have. So this repair is an operator action, permanently, and it is the ONLY
part of the fix that is: the gateway has already written and **test-run** a
launcher in its own state dir, and the page prints the exact `ExecStart=` for
it.

**The repair, on the box, once.** The Hardware page prints these with the real
unit name and paths filled in — prefer them over the shape below, which is what
production's own `empyralis-gateway-channels.service` needs:

```bash
sudo mkdir -p /etc/systemd/system/empyralis-gateway-channels.service.d
sudo tee /etc/systemd/system/empyralis-gateway-channels.service.d/empyralis-updatable.conf >/dev/null <<'EOF'
[Service]
ExecStart=
ExecStart=/var/lib/empyralis-gw/state/launch/run-gateway
Restart=always
EOF
sudo systemctl daemon-reload
sudo systemctl restart empyralis-gateway-channels.service
```

Three things about that block are deliberate, not style:

- **A drop-in, never `sed` over the shipped unit.** The empty `ExecStart=` is
  systemd's own documented way to reset the list, so it works whether the unit
  declares one `ExecStart` or several; reverting is `rm` on one file plus a
  `daemon-reload`, rather than reconstructing a line from memory.
- **`Restart=always` is part of the same edit.** Production carries
  `Restart=on-failure`, under which the clean exit a self-update ends with
  leaves the gateway switched OFF rather than starting the new build. Fixing
  the path alone would trade a stale box for a dead one.
- **The launcher is boot-safe by construction.** It prefers the release layout
  when a build is actually staged there and otherwise execs the exact path the
  gateway was running from when it wrote the file — so on a box that has never
  self-updated, the restart above starts precisely what is running today. The
  gateway also runs it once in probe mode before the page ever offers it, and
  the page shows the commands only when that probe succeeded.

**Verify:**

```bash
systemctl show -p ExecStart -p Restart --value empyralis-gateway-channels.service
systemctl is-active empyralis-gateway-channels.service     # → active
```

then reload the Hardware page: the row goes back to "Up to date" (or offers a
real update) on the gateway's next reconnect, with nothing to clear.

**Rollback**, if the service does not come back:

```bash
sudo rm /etc/systemd/system/empyralis-gateway-channels.service.d/empyralis-updatable.conf
sudo systemctl daemon-reload
sudo systemctl restart empyralis-gateway-channels.service
```

**Boxes provisioned by `scripts/install-agent-computer.sh` or the Packer image
need NOTHING** — both write a `run-gateway` launcher that already checks the
release layout first (since 2026-07-21 / 2026-07-29 respectively), and the
gateway reports them as updatable. Only hand-installed boxes, and boxes
installed before those dates, need this.

## 4. Known gaps — deliberate, not fixed in this pass

- **Redis is not running on the VPS**, and `EMPYRALIS_SKIP_REDIS_CHECK=true` is currently set to paper over that. Two real options, not a default to silently pick: (a) install and run Redis on the box, unset the skip flag, or (b) confirm nothing production-critical actually needs Redis on this deployment and leave the skip flag set *deliberately*, with that decision written down somewhere better than a stale env var. Not resolved here because it's a real infrastructure decision, not a bug fix.
- **Backend runs as root via pm2**, not as the dedicated `empyralis` user that already exists and owns `.env`. `deploy/empyralis-backend.service` is the fix; migrating is Section 2 step 7's optional path, intentionally not folded into routine deploys.
- **One dead credential is still in git history** (an expired Vercel OIDC token, `bb44160c0`/`8e226bd6e` — dead since 2026-06-28, already untracked from the working tree). Fully purging it needs a `git filter-repo`/BFG pass and a force-push across ~10 branches/tags — deliberately not done without a separate go-ahead, since it's destructive and the token is already inert.
- **The orphaned continue-intent flow** (`frontend/app/continue/page.tsx` writes a sessionStorage key that nothing reads anymore, since the dead-code reader was removed alongside the landing page) — pre-existing, unrelated to this pass, flagged for a future look.
- **Stale Server Action ID errors** in frontend logs from browser tabs left open across a deploy — expected Next.js behavior, not a bug; they stop once the tab reloads.

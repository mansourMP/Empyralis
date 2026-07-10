# Deploy Runbook — empyralis.ai

**Reality, not aspiration.** Production is a single DigitalOcean VPS (`165.227.25.201`, Ubuntu 24.04, droplet `ubuntu-s-1vcpu-2gb-sfo2`), not Render and not Docker — `render.yaml` and the app-level Dockerfiles were deleted 2026-07-10 because neither was actually in use. One git checkout at `/opt/empyralis-app` holds both the backend (repo root) and frontend (`frontend/` inside it). nginx terminates TLS (Certbot) and reverse-proxies to two local processes: FastAPI/uvicorn on `:8001`, Next.js on `:3000`. Postgres runs locally on the box. Redis does **not** run today (see Known Gaps). The Rust runtime kernel (`empyralis-runtime-kernel`, gates scheduling/governance decisions) **is** already built and configured — a real release binary at `/opt/empyralis-app/empyralis-runtime-kernel/target/release/empyralis-runtime-kernel`, `EMPYRALIS_RUNTIME_KERNEL_BIN` already set in `.env` — confirmed present 2026-07-10, no action needed. (There are also two apparent leftovers, `/opt/empyralis.OLD` and `/root/empyralis`, not investigated this pass — check before assuming either is safe to delete.)

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
| `EMPYRALIS_PUBLIC_API_URL` | `https://empyralis.ai` | Must be a real HTTPS URL, not loopback — this is a *different* concept from the frontend's `EMPYRALIS_API_URL=http://127.0.0.1:8001` (that one's the internal same-box hop; this one's "what's the publicly reachable API URL," used for links/webhooks/callbacks external services need). |

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

### Provider keys, secrets (values are real credentials — never commit)

`ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `OPENAI_API_KEY` / others as configured providers require it · `DATABASE_URL` (local Postgres) · `ORION_JWT_SECRET` (persisted at `~/.empyralis/state/auth/jwt_secret` — do not rotate casually, it invalidates every live session) · `CREDENTIAL_VAULT_KEY` · `EMPYRALIS_SECRETS_BROKER_SECRET` / `EMPYRALIS_TOOL_BROKER_SECRET` (BYOK credential storage refuses to start without these) · `EMPYRALIS_TELEGRAM_HOSTED_BOT_TOKEN` (confirmed this session: never committed to git, lives only in this file).

### Frontend (`/opt/empyralis-app/frontend`, or via `deploy/empyralis-frontend.service`'s `Environment=` lines)

| Var | Value |
|---|---|
| `EMPYRALIS_API_URL` | `http://127.0.0.1:8001` — same-box loopback is correct and code-supported (explicit localhost exemption from the HTTPS-required check), do not point this at the public domain. |
| `NODE_ENV` | `production` |

## 2. Deploy steps, in order

1. **Pre-flight, once per box (skip if already done — check `git log -1` on both):**
   ```bash
   ssh root@165.227.25.201
   cd /opt/empyralis-app
   git log -1 --oneline   # compare against your local HEAD before pulling
   ```
2. **Pull code:**
   ```bash
   git fetch origin && git checkout verify && git pull
   ```
3. **Backend deps:**
   ```bash
   .venv/bin/pip install -r requirements.txt -r requirements-worker.txt
   ```
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
   ```bash
   # routine deploy, current mechanism:
   pm2 restart empyralis
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

## 4. Known gaps — deliberate, not fixed in this pass

- **Redis is not running on the VPS**, and `EMPYRALIS_SKIP_REDIS_CHECK=true` is currently set to paper over that. Two real options, not a default to silently pick: (a) install and run Redis on the box, unset the skip flag, or (b) confirm nothing production-critical actually needs Redis on this deployment and leave the skip flag set *deliberately*, with that decision written down somewhere better than a stale env var. Not resolved here because it's a real infrastructure decision, not a bug fix.
- **Backend runs as root via pm2**, not as the dedicated `empyralis` user that already exists and owns `.env`. `deploy/empyralis-backend.service` is the fix; migrating is Section 2 step 7's optional path, intentionally not folded into routine deploys.
- **One dead credential is still in git history** (an expired Vercel OIDC token, `bb44160c0`/`8e226bd6e` — dead since 2026-06-28, already untracked from the working tree). Fully purging it needs a `git filter-repo`/BFG pass and a force-push across ~10 branches/tags — deliberately not done without a separate go-ahead, since it's destructive and the token is already inert.
- **The orphaned continue-intent flow** (`frontend/app/continue/page.tsx` writes a sessionStorage key that nothing reads anymore, since the dead-code reader was removed alongside the landing page) — pre-existing, unrelated to this pass, flagged for a future look.
- **Stale Server Action ID errors** in frontend logs from browser tabs left open across a deploy — expected Next.js behavior, not a bug; they stop once the tab reloads.

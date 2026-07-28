# Signup email verification — plan

## What exists today

- `/auth/signup` (`server_modules/routes_auth.py`) → `register_user()` in `server_modules/auth.py`
  creates the account, tenant, workspace, and password identity, then immediately
  issues session cookies. There is no verification step of any kind — an account
  is fully live, with full access, the instant the form is submitted.
- The platform has **zero email-sending capability**. Confirmed by grep across
  `server_modules/` and `frontend/`: no SMTP client, no Resend/Postmark/SES SDK,
  nothing. Workspace invites (`routes_workspaces.py`) are link-only for exactly
  this reason — there was never anywhere to send a link *to*.
- Short-lived, secret-bearing codes already exist for other flows and set the
  house style to follow: `pilot_invite_service.py` generates a code with
  `secrets.token_urlsafe`, stores it via `control_plane_repository.py` with an
  expiry/use-count, and exposes create/validate/claim helpers. Password hashing
  (`auth._hash_password`) uses PBKDF2 with a per-value salt. Third-party API
  integrations (`multimodal_provider_service.py`) read a key from an env var and
  raise a typed `*Unavailable` error with a logged message when it's missing —
  never a silent no-op. This is the convention this feature follows throughout.

## What we're building (v1 scope)

Standard deferred-verification pattern, not a hard email-first gate:

1. Signup creates the account exactly as it does today — unchanged latency,
   no blocking round-trip to an email provider.
2. Immediately after, a 6-digit numeric code is generated, hashed, stored with
   a 20-minute expiry (`EMAIL_VERIFICATION_CODE_TTL_MINUTES`, default 20), and
   emailed to the signup address.
3. The frontend sends a brand-new signup straight to a "check your email"
   screen (`/verify-email`) instead of the app. Entering the correct code is
   required to proceed past that screen. Resend is available, rate-limited
   both at the HTTP layer (existing IP-window limiter, new
   `auth_email_verification` quota profile) and per-code (60s minimum between
   resends, 5 wrong-guess attempts before a code is dead and a new one is
   required).
4. A code that expires or runs out of attempts is not silently reusable —
   the user must explicitly request a new one.

### Deliberately out of scope for v1

- **Server-side hard-blocking of every authenticated route for unverified
  users.** The founder said full-block is fine and to lean that way unless
  there's a good reason not to — there is one here: `auth.py` (~6,300 lines)
  and the account-shell payload builder it feeds
  (`account_shell_service.py`) are the single most shared, most concurrently
  edited surface in this codebase tonight (several other agents are active
  in sibling worktrees). Threading a new field through
  `_auth_payload_for_user`, which is called from login, register, refresh,
  and `/auth/me` alike, is exactly the kind of change that's easy to get
  subtly wrong across four call sites at once, for a feature that cannot be
  fully verified end-to-end tonight anyway (no real API key — see below).
  The actual threat this feature exists to address — a brand-new signup
  with an unconfirmed or mistyped address — is fully handled by the
  frontend redirect straight to `/verify-email` after signup; a user who
  never passes that screen never sees the app's first render for this
  session.
  What *is* shipped: `email_verified` status is queryable
  (`GET /auth/verify-email/status`) and enforced wherever the new endpoints
  themselves are hit, so hardening every route later is additive, not a
  redesign.
- **Blocking already-verified, already-logged-in users retroactively.**
  Accounts that existed before this shipped, or that never got a
  verification row for any reason, read as verified (fail open on the
  *read* side — see `email_verification_service.verification_status`,
  `"none"` counts as verified). Only new signups go through the gate.

## Provider choice: Resend

Evaluated three:

- **Resend** — API is a single `POST /emails` call with a JSON body
  (`from`/`to`/`subject`/`html`), a normal Bearer API key, a generous free
  tier (3,000 emails/month, 100/day), and first-class Next.js/TypeScript
  ergonomics if the frontend ever wants to send directly. Sending domain
  verification is a few DNS records. This is the whole integration surface —
  no SDK required, `httpx.post` is enough, which matches how this codebase
  already talks to third-party model/voice providers
  (`multimodal_provider_service.py` calls OpenAI/ElevenLabs over raw
  `httpx`, no SDK).
- **Postmark** — also simple and reliable (it's what a lot of transactional
  mail veterans reach for), but its free tier is a 100-email trial rather
  than an ongoing free allowance, and its account-approval process for
  transactional sending has historically been slower/stricter than Resend's
  for a brand-new sender identity. Solid option, just not the faster path
  for a small team standing this up tonight.
  
- **AWS SES** — cheapest at volume and fine if the rest of the stack were
  already on AWS, but it starts every new account in a sandbox that only
  sends to verified addresses until a manual "production access" request is
  approved (can take a day or more), and the setup (IAM, verified
  identities, region config) is real infrastructure work for a platform
  that currently has none of it. Wrong shape for "send a 6-digit code
  today."

**Decision: Resend.** Fastest path to a working sender for a team that has
never sent a transactional email before, free tier covers early-stage
volume outright, and the integration is a single authenticated POST — no
SDK, no sandbox approval wait, matching this codebase's existing
raw-`httpx` pattern for third-party calls.

## Config surface (new env vars, none of them set anywhere yet)

- `EMAIL_PROVIDER_API_KEY` — Resend API key. **Required.** Its absence is a
  hard, logged failure (`EmailProviderUnavailable`), never a silent no-op —
  same discipline as `OPENAI_API_KEY`/`ELEVENLABS_API_KEY` in
  `multimodal_provider_service.py`.
- `EMAIL_PROVIDER_FROM_ADDRESS` — optional, defaults to a placeholder
  `Empyralis <onboarding@empyralis.dev>`; must be swapped for a real
  domain-verified sender before this can send in production.
- `EMAIL_VERIFICATION_CODE_TTL_MINUTES` (default 20),
  `EMAIL_VERIFICATION_MAX_ATTEMPTS` (default 5),
  `EMAIL_VERIFICATION_MIN_RESEND_INTERVAL_SECONDS` (default 60) — all
  optional, sane defaults, override only if product wants different
  numbers.

## Data model

New table `email_verification_codes` (Postgres, with a matching SQLite
fallback table for local/dev — the same dual-path convention every other
table in `control_plane_repository.py` already uses): `id`, `user_id`,
`email`, `code_hash` (SHA-256 peppered with the JWT secret + user_id, never
plaintext), `status` (`pending`/`verified`), `attempts`, `max_attempts`,
`expires_at`, `created_at`, `updated_at`, `verified_at`. Lives alongside
`pilot_invites`/`workspace_member_invites` in the same repository file,
following the same placement convention.

## What needs the founder before this is "done," not just "built"

1. Create a Resend account and verify a sending domain.
2. Generate a Resend API key and set `EMAIL_PROVIDER_API_KEY` (and, once a
   real domain is verified, `EMAIL_PROVIDER_FROM_ADDRESS`) in the actual
   deploy environment.
3. Do one real signup end-to-end and confirm the code email actually lands
   (inbox, not spam) — nobody on this build had a live key to do that
   tonight, so the send path is implemented and unit-tested against a
   mocked Resend call, but never fired for real. See the Linear issue for
   exactly what was and wasn't verified.

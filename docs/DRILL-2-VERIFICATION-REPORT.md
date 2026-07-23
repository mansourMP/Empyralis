# Drill #2 — Fresh-Account Verification Report

> **OUTDATED (2026-07-23):** this is a point-in-time fresh-account drill from
> 2026-07-09 — its findings have since been folded into and superseded by
> `docs/PLATFORM-MAP.md`. It also predates the 2026-07-23 founder ruling that
> "Sage" is dead product terminology (the platform has only agents —
> owner-facing, customer-facing serving the owner, and AskAI); this document
> uses "Sage" throughout as a live concept. Kept for history; do not build
> from this.

**Date:** 2026-07-09 | **Branch:** `verify` | **Method:** real UI in browser (`web-verify2` :3200 / `backend-verify2` :8211), live backend, no code changes, no simulated turns. Account: `drill2-20260709-183237@empyralis-test.com` ("Drill Two Tester"), created fresh through the actual signup form.

This re-runs the Project #1 drill's 8-item friction log against current `main`-bound `verify` work (mandate, kill-switch, transparency-v1, attribution, first-run honesty, truth-in-numbers, Fleet consolidation) and adds four new checks for work that landed since: usage metering, inline rename, wizard placement, Cmd+K.

---

## Delta table

| # | Item | Then | Now | Evidence |
|---|------|------|-----|----------|
| 1 | Platform-brain first message | 402, no reply | **Fixed** | Fresh account auto-funded ($0.50 / 10,000 credits, `signup_grant`, confirmed live in `workspace_registry.metadata_json`). Asked Sage "what can you help me do here?" — got a full, real DeepSeek-generated reply, no error. |
| 2 | Unconfigured connectors | Looked live, failed on click | **Fixed** | Connectors tab: all 20+ entries (Google Workspace, GitHub, Notion, Linear, …) show "Not configured on this deployment," desaturated icons, `cursor: not-allowed`, click is a genuine no-op (verified via DOM inspection + click test, not just styling). |
| 3 | Kill switch | — | **Fixed** | "Stop agent" in Overview header → status flips to **Stopped**, banner "Stopped by drill2-...@empyralis-test.com," Resume button appears. Messaged the stopped agent → *"This agent is stopped right now — an owner needs to resume it before it can reply."* (verbatim match to `kill_switch_gate` copy). Resume → back to **Ready**. Settings → "Emergency stop" → "Stop all agents" present workspace-wide with correct copy. |
| 4/5 | Activity + attribution | Blended into Sage / empty | **Fixed**, with one gap | A successful turn and a kill-switch-blocked turn both appear on the agent's own Overview feed as `specialist_activity` under **"Meridian chat completed"** / **"Meridian chat failed"** — not Sage's identity. **Gap (new):** a turn that fails for the *tool-blocked* reason (see #6) writes nothing to the ledger at all — confirmed via direct `agent-activity` API poll, survived a page reload. |
| 6 | Tools | Blank toolset, toggle no-op | **Fixed** for defaults, **not fixed** for direct-chat honesty | Standard preset seeds exactly 3/20 tools (Web Search, Memory Read, Memory Write) on by default; Tools tab reflects true state. Asked the agent to check a calendar (Calendar Access is off) via **direct chat** → got the generic **"Something went wrong. Try again."**, not the honest *"This agent doesn't have every tool turned on…"* message. Root cause: `TOOLS_LIMITED_NO_REPLY` is wired only into `sage_agent_runtime_service.py` (Sage's own chat loop); `direct_chat_generation_service.py` (the path "Message {agent}" actually uses) has no equivalent. Control test (plain "say hello") replied normally, isolating this to the blocked-tool case specifically. |
| 7 | Mandate UI | Missing | **Confirmed still missing** | No mandate/audience-tools surface anywhere — not in Settings, not in any of the 8 agent-detail tabs. Matches the plan: real, enforced backend, zero frontend surface. |
| 8 | Status | "Active" | **Fixed** | Freshly created agent reads **Ready** in both the Agents-list row and the Overview status chip, everywhere I looked. |

## New checks (work that landed since Drill #1)

| Check | Result |
|---|---|
| Cost/tokens off $0.00 | **Partially fixed.** Billing page is fully live and accurate: $0.0007 total this month, 4,994 tokens, 2 LLM calls, correctly split Sage ($0.0005) vs. Meridian Support ($0.0002 under project "General"). The agent's own **Model tab** also shows the real per-agent "Cost today" ($0.0002). **But** the **Agents list** and the **Project agent-list row** both still show **$0.00** and **"never"** for last-active, even after a hard reload — confirmed the backend has the right data (`GET /fleet/usage` returns it correctly); the list view's `last_activity`/cost join just isn't picking it up. First screen a user sees is still lying. |
| Inline rename | **Fixed.** Click-to-edit on the Overview title works, updates the title and breadcrumb immediately, persists (`Meridian` → `Meridian Support`). |
| Wizard placement, 3-in-a-row | **Fixed.** Step 1 offers Cloud / Self-hosted VPS / This computer; picking Cloud (default) and completing the wizard resolves to Placement: **Cloud** on the agent's Overview — no "Ready to configure" stranding. |
| Cmd+K jump to agent | **Fixed.** Opens instantly, lists the just-created agent by name under "Agents," selecting it navigates straight to its Overview tab. |

---

## New friction found (not on the original list)

**1. Fresh-signup CSRF lockout with no recovery path (Medium-High severity, narrow but real trigger).**
My first signup attempt through the real UI failed: `POST /api/auth/signup → 403 {"detail":"CSRF validation failed."}`, surfaced to the user as the unhelpful *"Couldn't create the account — Authentication could not finish. Try again when ready."* Root cause, confirmed by code + a clean `curl` control call (which succeeded instantly with zero cookies): both `frontend/lib/server/control-plane-proxy.ts` and `server_modules/auth.py::validate_csrf()` treat the mere *presence* of an `empyralis_access_token`/`empyralis_refresh_token` cookie — regardless of validity — as "there's a live session," and then hard-require a matching CSRF cookie. If a browser is carrying a stale access/refresh cookie with no matching CSRF cookie (exactly what a long-lived local dev/test browser profile accumulates over weeks of sessions across ports/restarts), **every state-changing auth call — signup, login, and logout — 403s identically, and there is no in-product way out.** The only paths I found were (a) let the ~1h access-token cookie expire, which doesn't help if the refresh cookie is still "structurally valid," or (b) know it's a cookie problem and manually clear site data. A real user hitting this has no error message telling them what's wrong or how to fix it, and the logout button — the obvious thing to try — fails the same way. I only got past it by scripting a matching CSRF cookie in via a diagnostic curl call; that's not something an end user can do.

**2. Wizard's Channels step is only half as honest as the Connectors step.** Slack/Discord show "Not configured here" (correct, greyed), but unlike the Connectors tab they're still clickable and open a real "Connect Slack" panel with an enabled button; only after clicking "Connect Slack" do you get the (accurate but developer-facing) *"Slack OAuth is not configured. Set SLACK_CLIENT_ID and SLACK_CLIENT_SECRET."* An owner shouldn't reach an env-var name. Minor relative to #1, but it's the same "visibly inert before click" bar #2 on the original list already cleared for Connectors — Channels didn't quite get there.

**3. Activity feed is dominated by its own polling.** The Overview "Recent activity" feed logs every `GET agent-activity` fetch as its own `fleet_control` event ("Fleet: get_agent_activity → ainstall_…"), so a real action like "Meridian chat completed" gets buried under a wall of read-events within seconds, on a page that auto-polls. Not incorrect, but undermines the feed as a transparency surface — signal-to-noise is bad enough that a real customer conversation could be hard to spot.

**4. Debug instrumentation left in a live path.** `sage_agent_runtime_service.py`'s action-loop function still has `_s2.stderr.write(f"DEBUG EVENT ...")` / `DEBUG ACCUMULATED` / `DEBUG ACTION LOOP` prints on the hot path for every Sage turn. Doesn't affect the user, but it's shipping debug output on every request.

---

## Top 5 fixes (ranked)

1. **Extend the truth-in-numbers tool-blocked handling from Sage-only to direct/specialist chat.** This is the path real end-customers actually hit (per the product's own pitch: "it gets its own Telegram bot… handles your customers end to end"). Right now a blocked-tool turn on a specialist agent gets a generic, unhelpful error *and* vanishes from the activity ledger — the exact "instruments lied" failure mode the last pass explicitly set out to fix, just not on this surface.
2. **Wire real usage/last-activity into the Agents list and Project list rows.** The data is correct and available (`GET /fleet/usage` proves it); it's a display-layer gap. This is the first screen every user sees, and it's currently telling them $0.00 and "never" while the agent detail page one click away tells the truth.
3. **Give a stuck browser a way back in.** At minimum, a distinct error message when CSRF fails due to a stale/mismatched cookie ("Your session looks out of date — clear your cookies for this site and try again" beats a generic auth failure), and ideally treat a present-but-unverifiable access/refresh cookie the same as an absent one for signup/login purposes, not just for the already-lenient logout/refresh path.
4. **Pre-disable unconfigured Channels the same way Connectors already are**, instead of letting the click-through reach a raw env-var error.
5. **Thin out the activity ledger's read-noise** (or filter `get_agent_activity`/similar read-only fleet_control events out of the *displayed* feed) so the transparency surface actually surfaces the interesting events.

Carried forward, unchanged: production deploy, per-agent channel identities, channel health alerts, mandate/schedule UI (#7 above) remain the biggest blockers to a real user, per `docs/PLATFORM-MAP.md` Part 9.2.

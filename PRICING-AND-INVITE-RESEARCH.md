# Invite flows & seat pricing — how the field actually does it

> ## ⚠️ READ THIS BEFORE USING ANY NUMBER BELOW
>
> **Checked on 2026-08-20. Every figure here is a snapshot, not present truth.**
>
> CLAUDE.md's own rule is that design/audit/research documents are NOT kept in this
> repo, because they go stale within a week and then get cited as fact. **This file is
> a deliberate, founder-requested exception and it has a hard shelf life.**
>
> ```
> Pricing pages change quietly and without announcement.
> A confidently wrong number here is WORSE than no number.
>
> BEFORE you use any figure in this file to make a decision:
>   1. open the vendor's own pricing page (URLs in the appendix)
>   2. re-read the number
>   3. if it moved, fix this file or delete it
> ```
>
> Anything I could not verify from the **vendor's own page** is marked
> `COULD NOT VERIFY` or `SECONDARY SOURCE`. Those are not soft warnings — treat them
> as unknown.

---

# PART 1 — How the field handles an invited user with no account

## The five questions, answered per vendor

| | **Linear** | **Slack** | **Notion** | **ChatGPT Business** |
|---|---|---|---|---|
| **B signs up from an invite — what happens?** | Prompted to join the inviting workspace *during onboarding*: "Users who are creating new accounts will see a prompt to join the workspace during the onboarding flow." | Must create an account **scoped to that workspace**: "you'll need to accept the invitation and set up a Slack account for that workspace." | Emailed "a link to sign in and join your workspace"; "If they don't already use Notion, they'll need to sign up to access your page." | An account is **created for them as part of joining**: "New users who do not have ChatGPT accounts will have one created for them as a part of joining the ChatGPT Business workspace." |
| **Do they also get their own workspace?** | Not automatic. Signup = "create a workspace" **or** join one you're invited to. | **No personal workspace concept at all.** "you can use the same email address to join as many workspaces as you'd like, but you'll have **separate Slack accounts for each one**." | Yes — a personal workspace exists alongside; one account spans many: "your workspace switcher will display all the workspaces associated with your email address." | Yes, and it's an explicit choice: users "have the option of keeping their personal and Business workspaces separate, or **merge** them." |
| **Explicit accept step, or silent join?** | **Explicit.** Invite is an action the user takes ("Create or join a workspace" → accept). | **Explicit.** "Join Now" button in the email, then name entry → account created. | **Explicit.** "it will show as **pending** until the [invitee] accepts." | **Explicit** — accepting the invitation is what triggers account creation. |
| **If B declines / never accepts?** | `COULD NOT VERIFY` — Linear docs do not describe a decline outcome. | Invite simply sits there. **Not billed:** "you will not be billed for invited members until they become active in Slack." Invite links expire after 30 days; an admin can revoke. Unsubscribing from the emails "won't affect their invitation." | `COULD NOT VERIFY` — stays `pending` in Settings → Members; no documented decline outcome. | `COULD NOT VERIFY` — no documented decline path. |
| **Where does a pending invite surface for an existing user?** | **Badge on the workspace switcher.** "If you see a number next to *Create or join a workspace*, it's possible you have a pending workspace invite and can accept this to join." | Email; plus workspace switcher after joining. | **Admin side only** (Settings → Members shows pending). Invitee side is the email. | Email; then workspace picker in the profile menu / mobile sidebar. |

## What the field converges on

```
1. AN INVITE IS ALWAYS AN EXPLICIT ACT.        4 of 4. Nobody silently grants
   Click a link → answer a question → in.         membership on first sign-in.

2. THE INVITE IS THE ONBOARDING, NOT A          Linear: prompt inside the signup
   BANNER BOLTED ON AFTERWARDS.                    flow. OpenAI: the account is
                                                   created BY the accept.

3. AN INVITED SIGNUP DOES NOT GET HANDED        Slack has no personal workspace
   A SECOND, EMPTY WORKSPACE.                      at all. Linear makes it a
                                                   choice, not a side effect.

4. A PENDING INVITE IS NOT BILLED.              Slack states it outright.
   Membership begins at ACCEPT, not at INVITE.

5. NOBODY DOCUMENTS DECLINE.                    3 of 4 silent. This is a real
   The field's answer is "it just sits there."     gap in the field, not in us.
```

**One divergence worth naming:** Slack is the outlier that proves the rule the hardest
— it has no personal-workspace concept whatsoever, so "invited user gets a phantom
workspace" is structurally impossible there. Notion and OpenAI both have a personal
space *and* an org space, and both make the relationship between them an explicit
user choice rather than an automatic side effect.

## Recommendation for Empyralis

**The fix that just landed (`e28f8dba`) matches the field. Keep it. Two adjustments.**

Scoreboard against the five convergence points:

```
1. explicit act ................ ✓ Join / Decline card, no silent accept
2. invite IS the onboarding .... ✓ with no workspace of your own the invite
                                    gets the whole page, not a strip above a
                                    "Create a workspace" form
3. no phantom workspace ........ ✓ bootstrap_personal_workspace=False
4. pending ≠ billed ............ ✓ (nothing bills yet — keep it that way)
5. decline ..................... ✓ we're AHEAD of the field here
```

**Adjustment 1 — the decline path is more generous than the field, and that is
correct; do not "fix" it back.** Today: decline → next login mints their own
workspace. Slack would leave them with nothing. But CLAUDE.md's positioning says
*the workspace is the product* — an account that owns no workspace can do nothing,
so Slack's answer would make Decline a one-way door out of your own account. The
commit already reasons this way. **Leave it. Write it down so nobody "aligns with
Slack" later.**

**Adjustment 2 — surface pending invites the way Linear does: a count on the
workspace switcher, not only a banner.** Linear's mechanic (a number badge next to
"Create or join a workspace") is the one thing in this comparison we don't have. The
existing `PendingWorkspaceInvitesBanner` covers the *memberless* invitee. A person
who **already has a workspace** and gets invited to a second one is the case that
needs the switcher badge — otherwise the only notification is an email, which is
exactly the surface Notion is weakest on.

**On the Teams question, which is where CLAUDE.md's model helps us:** every vendor
here has a two-level model (workspace → team/space) and every one of them has to
answer "which teams does the invitee join?" — Linear even shipped a dedicated dialog
for it. **Empyralis has no Teams layer; projects hold members directly.** So the
invite carries a *project* grant, not a team-selection ceremony. Do not add a
"choose your teams" step to the accept flow. One card, two buttons, done.

---

# PART 2 — Seat & subscription pricing, current numbers

## Collaboration / PM tools

| Vendor | Tier | Monthly billing | Annual billing | Free tier | Metered usage on top of seats |
|---|---|---|---|---|---|
| **Linear** | Free | $0 | $0 | Unlimited members · 2 teams · 250 issues · Agent platform · Linear Agent | AI credits (see below) |
| | Basic | `COULD NOT VERIFY`¹ | **$10** /user/mo | | |
| | Business | `COULD NOT VERIFY`¹ | **$16** /user/mo | | |
| | Enterprise | — | Custom, annual only | | |
| **Notion** | Free | $0 | $0 | Trial AI · basic forms · publish to web · Calendar · databases · **up to 5 free guests** | Notion AI credits (see below) |
| | Plus | **$10** /member/mo | ~20% off | | |
| | Business | **$20** /member/mo | ~20% off | | |
| | Enterprise | Custom | Custom | | |
| **Slack** | Free | $0 | $0 | 90 days history · 10 apps · 1:1 huddles · 1:1 Slack Connect · basic AI | None published on the pricing page |
| | Pro | **$8.75** /user/mo | **$7.25** /user/mo | | |
| | Business+ | **$18** /user/mo | **$15** /user/mo | | |
| | Enterprise+ | Contact sales | Contact sales | | |

¹ `linear.app/pricing` displays **annual-billed rates only**. A separate monthly rate
is not shown on the page. Do not assume $10/$16 are the monthly-billing prices.

## AI products with team plans

| Vendor | Tier | Monthly billing | Annual billing | Included usage | Overage |
|---|---|---|---|---|---|
| **ChatGPT Business** | Standard seat | **$25** /user/mo | **$20** /user/mo | per-seat limits on advanced features | draws from a **shared, workspace-level credit pool** |
| | Premium seat | **$125** /user/mo | **$100** /user/mo | 5× Standard, no 5-hour limit | same pool |
| | | *min. 2 standard seats · seat types mix in one workspace* | | | Business credits valid 12 months, **non-refundable** |
| **Claude Team** | Standard seat | **$25** /mo | **$20** /mo | usage limits per plan | Enterprise self-serve = "$20/seat plus usage costs" at API rates |
| | Premium seat | **$125** /mo | **$100** /mo | | |
| | | *2–150 seats · "mix and match seat types"* | | | |
| **Claude individual** | Pro | $20/mo | $17/mo ($200 upfront) | — | — |
| | Max | from $100/mo | — | 5× or 20× Pro | — |
| **Cursor** | Hobby | $0 | — | "Limited" | — |
| | Pro | **$20** /mo | — | **$20** of model usage | on-demand, **billed in arrears** |
| | Pro+ | **$60** /mo | — | **$70** of model usage | " |
| | Ultra | **$200** /mo | — | **$400** of model usage | " |
| | Teams Standard | **$40** /user/mo | — | "Standard team allowance", **per-user, does not transfer between members**, resets each cycle | " |
| | Teams Premium | **$120** /user/mo | — | 5× Standard allowance | " |
| | Enterprise | Custom | — | **pooled usage** | " |
| **Devin** | `SECONDARY SOURCE — see warning` | | | | |

### ⚠️ Devin — could NOT be verified from the vendor's own page

`https://devin.ai/pricing` returned **HTTP 429 (rate limited)** on four separate
attempts on 2026-08-20. `docs.devin.ai/admin/billing` loads but **publishes no
prices** — it says only that self-serve is "a mix of included quota and on-demand
credits" and that Enterprise is "billed in Agent Compute Units (ACUs) at the rate set
in their order form."

Third-party blogs (Lindy, AIToolPick, others) consistently report **$20/mo Core,
$500/mo Team with 250 ACUs, ~$2.25/ACU pay-as-you-go, ~$2.00/ACU on Team**.
**Do not use these numbers for a decision.** They are secondary, uncorroborated by
Cognition, and this file does not vouch for them.

What *is* verified from Devin's own docs and is the useful part anyway: **Devin's
unit of billing is agent work (ACUs), not seats.**

### Other patterns worth stealing from

| Vendor | Pattern | Why it matters to us |
|---|---|---|
| **Slack — Fair Billing** | "Paid members are only billable if they're actively using Slack." Inactive 28+ days → **prorated credit back**. Deactivate mid-cycle → credit deposited next day. And: **"you will not be billed for invited members until they become active."** | The single most direct answer to "a teammate who only reads shouldn't cost what an operator costs." Slack's answer is *don't charge for people who aren't there*. |
| **Notion — guests are free** | Guests are page-scoped and **not billable**. Free plan: up to 5. Plus: up to 10. Members are billable; guests are not. | A second axis: *scope of access* decides price, not headcount. |
| **Figma — seat types** | Professional: Full **$16**, Dev **$12**, Collab **$3** /mo. Organization: **$55 / $25 / $5**. Enterprise: **$90 / $35 / $5**. And "you can let others view and comment on your files **without purchasing extra seats**." | The most granular version of "a reader costs less than an operator." Note the ratio: a Collab seat is **~1/5 to 1/18** of a Full seat. |
| **Vercel** | Pro **$20/mo per developer seat**, with **$20 of included credit** across resources; overage metered ($2/1M edge requests, $0.15/GB transfer, …). **Viewers: "Unlimited"**, not billed. | Closest structural match to us: a paid *operator* seat that carries a usage wallet, plus **free unlimited viewers**. |
| **Linear — AI credits** | "billed based on actual usage, not a fixed per-seat fee. Charges are deducted from a **prepaid, workspace-level balance**… **pooled across the workspace**." **Opt-in** — you must add funds first. $10 min top-up, $50 min auto-reload. Loop run $0.07–$0.20; coding session $0.50–$1; bug fix $3–$5+. | This is the closest thing in the field to what Empyralis needs, from the company we most want to resemble. Seats buy the *product*; a separate **pooled prepaid wallet** buys the *compute*. |

## Who charges what — the shape of the field

```
PURE PER-SEAT ......... Slack (+ fair billing), Linear (product), Notion (product)
PURE USAGE ............ Devin (ACUs), Linear AI credits, Notion AI credits
BOTH, POOLED .......... ChatGPT Business (seat + shared workspace credit pool)
                        Vercel (seat + wallet), Cursor Enterprise (pooled)
BOTH, PER-SEAT WALLET . Cursor Teams — allowance is per-user and
                        "does not transfer between team members"   ← the bad version
```

**The most important single line in this whole document:**

> Cursor's team usage is **per-seat and non-transferable**. ChatGPT Business,
> Vercel, Linear and Cursor *Enterprise* all pool it at the workspace.
> **Pooling is where the field is going.** A per-seat wallet punishes exactly the
> team shape we're selling to — one operator running agents, four people reading
> the output — because four unused wallets sit idle while the operator hits a wall.

---

## Recommendation for Empyralis

### The metering line

```
      ┌──────────────────────────────────────────────────────────┐
      │  FULL ON EVERY TIER, NEVER METERED, NEVER THE PAYWALL    │
      │  workspace · projects · documents · tasks · members ·    │
      │  invites · revision history · channels · MCP · search    │
      │  ── this is the CONTEXT LAYER. it is the product. ──     │
      └──────────────────────────────────────────────────────────┘
                                  │
                                  │  the line sits HERE, and only here
                                  ▼
      ┌──────────────────────────────────────────────────────────┐
      │  METERED, BECAUSE IT IS REAL COGS                        │
      │  (a) AGENT COMPUTERS — droplets we pay for by the hour   │
      │  (b) HOSTED AI — tokens we pay a provider for            │
      └──────────────────────────────────────────────────────────┘
```

Two COGS, two units. Nothing else has a marginal cost, so nothing else gets metered.
This is Linear's split exactly (subscription buys the product; a **prepaid pooled
wallet** buys the AI), and Linear is the company CLAUDE.md already names as the
positioning reference.

### The recommendation — one shape, not a menu

**Three tiers priced on AGENT CAPACITY. Seats are free and unlimited on every tier.**

| | **Solo** | **Team** | **Scale** |
|---|---|---|---|
| Price | **$20** /mo | **$100** /mo | **$200** /mo |
| Members | **unlimited, free** | **unlimited, free** | **unlimited, free** |
| Workspace / projects / documents / tasks | **full** | **full** | **full** |
| Agent Computers included | **1** | **3** | **8** |
| Additional Agent Computer | metered at cost + margin | " | " |
| Hosted-AI credits included | small starter grant | larger grant | larger grant |
| Extra AI | **prepaid pooled wallet**, workspace-level | " | " |
| BYO subscription / BYO key | **yes, on every tier** | yes | yes |

**Why this shape and not the alternatives:**

1. **The metered unit is the Agent Computer, not the person.** It is the only thing
   in the product with a per-unit cost that scales with what a customer actually
   does. A droplet costs money whether one person or nine watch its output.
2. **Seats are free because seats have no COGS.** The founder rejected per-seat out
   loud and he is right on the economics: a teammate reading a task costs us a
   database row. Slack's Fair Billing and Vercel's unlimited viewers are the field
   agreeing with him — both went out of their way to *stop* charging for presence.
3. **The AI wallet is POOLED at the workspace and PREPAID.** Copy Linear, not Cursor
   Teams. Pooled means one operator can burn the whole balance while four readers
   burn nothing — which is exactly the shape of our target customer. Prepaid means
   no surprise invoice, which for a solo founder buying a $20 plan is the difference
   between trying it and not.
4. **The tiers are ladders of capacity, not ladders of features.** CLAUDE.md's law:
   context is never the paywall. So Scale must not have a *document* feature Solo
   lacks. It has more machines.

**Risks, named honestly:**

| Option | Risk |
|---|---|
| **This one (capacity tiers, free seats)** | **Revenue does not grow with team size.** A 40-person company on one Agent Computer pays $20. Mitigation: they won't stay on one — the whole product thesis is that agents multiply. If that thesis is wrong, the pricing is the least of the problems. Second risk: an included droplet at $20 means **negative margin** if the customer runs a 2GB box 24/7 — verify against the real DigitalOcean bill before committing to $20, and consider making the $20 tier's included Agent Computer *cloud-only or sleep-on-idle*. |
| Per-seat (Linear/Slack shape) | Rejected by the founder, and correctly: it taxes the reader, which is the person we most want in the workspace because they're the one who makes it multiplayer. Also directly contradicts "the workspace is the product." |
| Pure usage, no subscription (Devin shape) | No revenue floor, no predictability for the customer, and it makes the *context layer* — the thing that doesn't commoditize — feel free and therefore worthless. Wrong story for our positioning. |
| Per-seat wallets (Cursor Teams shape) | Strictly worse than pooling for our exact customer shape. Do not do this. |

### "If I pay and invite 5 teammates, what do they cost?"

**Zero. On every tier. That is the answer, and it should be a marketing line.**

```
  owner pays $100 (Team)
    ├── teammate reads tasks & documents .............. $0
    ├── teammate comments, creates tasks .............. $0
    ├── teammate invites another teammate ............. $0
    └── teammate runs an agent  ─────────────────────────┐
                                                         │
        ...draws from the SAME workspace-level pooled    │
           AI balance and the SAME included Agent        │
           Computers the owner is already paying for  ◄──┘
```

The workspace pays for **capacity**. People are free; **work** is metered. A
teammate who only reads costs nothing because they consume nothing. A teammate who
runs agents hard shows up as the workspace's wallet draining faster — which is the
honest signal, and it arrives on the *owner's* bill where the budget actually lives.

Practical consequences to build against:

- **Never bill a pending invite.** Membership begins at accept. Slack states this
  explicitly and it is the right default.
- **Wallet spend must be attributable per member** even though it is pooled — the
  owner needs to see *who* burned it. Pooled billing, itemized reporting.
- **A per-member spend cap is the escape hatch**, not a per-member wallet. Opt-in,
  off by default. (ChatGPT Business does exactly this: shared pool + spend controls.)

### Before any of this is priced for real

- [ ] **Verify the $20 tier's droplet margin** against a real DigitalOcean invoice
      for a month of a `s-1vcpu-2gb` box. MAN-318 measured the *capacity*; nobody has
      measured the *bill*.
- [ ] **Polar, not Stripe** — CLAUDE.md is explicit. Verify Polar supports prepaid
      wallet top-ups and metered/usage billing *before* the wallet design is frozen.
      `COULD NOT VERIFY` in this pass; not researched here.
- [ ] Re-check every number in this file against the vendor's own page.

---

# Appendix — checked on 2026-08-20, from these URLs

| Claim | URL | Result |
|---|---|---|
| Linear tiers, $10 / $16 annual, Free plan contents, "Requires AI credits" | https://linear.app/pricing | ✅ fetched |
| Linear AI credits: prepaid, workspace-pooled, opt-in, $10 min / $50 auto-reload, per-run costs | https://linear.app/docs/ai-credits | ✅ fetched |
| Linear invite: new-account onboarding prompt; switcher badge for pending invite | https://linear.app/docs/invite-members | ✅ fetched (+ search snippets from same domain) |
| Linear billing page — monthly rates | https://linear.app/docs/billing-and-plans | ⚠️ fetched, contains **no prices** (defers to /pricing) |
| Notion tiers $10 / $20, free tier, AI credits "$10 per 1,000 monthly Notion credits" | https://www.notion.com/pricing | ✅ fetched |
| Notion invite: email link, must sign up, pending until accepted, Settings → Members | https://www.notion.com/help/add-members-admins-guests-and-groups | ✅ fetched |
| Notion: one account spans workspaces, "placed into the workspace as a paid member" | https://www.notion.com/help/create-delete-and-switch-workspaces | ✅ fetched |
| Notion: guests not billable, seat = member, mid-cycle proration | https://www.notion.com/help/members-and-billing | ✅ fetched |
| Notion guest limits (5 free / 10 Plus) | search over notion.com | ⚠️ **search snippets, not a direct page fetch** — re-verify |
| Slack tiers $8.75/$7.25, $18/$15, free plan limits | https://slack.com/pricing | ✅ fetched |
| Slack: separate account per workspace, "Join Now", explicit accept | https://slack.com/help/articles/212675257-Join-a-Slack-workspace | ✅ fetched |
| Slack: invited members not billed until active; revoke behaviour | https://slack.com/help/articles/360024686174-Invited-members-in-Slack | ✅ fetched |
| Slack Fair Billing: inactive 28+ days → prorated credit | https://slack.com/help/articles/218915077-Slack-Fair-Billing-Policy | ✅ fetched |
| Slack: 30-day invite expiry, unsubscribe ≠ decline | search over slack.com | ⚠️ **search snippets** — re-verify |
| ChatGPT Business $25/$20 standard, $125/$100 premium, min 2 seats, mixable | search over openai.com + help.openai.com | ⚠️ **search snippets only — openai.com and help.openai.com both returned HTTP 403 to direct fetch.** Re-verify manually in a browser. |
| ChatGPT credits: shared workspace pool, 12-month validity, non-refundable, spend controls | search over help.openai.com | ⚠️ **search snippets** — 403 on direct fetch |
| ChatGPT invite: account created on join; personal vs Business separate or merged | search over help.openai.com | ⚠️ **search snippets** — 403 on direct fetch |
| Claude: Pro $20/$17, Max from $100, Team $25/$20 std + $125/$100 premium, 2–150 seats | https://claude.com/pricing | ✅ fetched (redirect from anthropic.com/pricing) |
| Cursor: $20 / $60 / $200 with $20 / $70 / $400 included usage; Teams $40 & $120; on-demand billed in arrears | https://cursor.com/help/account-and-billing/pricing.md | ✅ fetched |
| Cursor Teams: usage per-user, non-transferable, resets each cycle | search over cursor.com | ⚠️ **search snippets** — re-verify at https://cursor.com/docs/account/teams/pricing |
| Figma seat prices: Full/Dev/Collab per plan; view+comment without extra seats | https://www.figma.com/pricing/ | ✅ fetched (View-seat price not listed on page) |
| Vercel: Pro $20/seat with $20 included credit, overage rates, unlimited free Viewers | https://vercel.com/pricing | ✅ fetched |
| Devin: self-serve = "included quota and on-demand credits"; Enterprise billed in ACUs per order form | https://docs.devin.ai/admin/billing | ✅ fetched — **publishes no prices** |
| Devin prices ($20 Core / $500 Team / ~$2.25 per ACU) | https://devin.ai/pricing | ❌ **HTTP 429 on four attempts. Numbers above are SECONDARY (third-party blogs) and must not be used for a decision.** |

## Fetches that failed, so nobody re-derives them

```
openai.com/chatgpt/pricing/            HTTP 403
openai.com/business/pricing/           HTTP 403
help.openai.com/en/articles/…          HTTP 403   (2 separate articles)
devin.ai/pricing                       HTTP 429   (×4, incl. trailing-slash variant)
docs.cursor.com/en/account/pricing     308 → cursor.com/docs
linear.app/docs/members-and-guests     HTTP 404
linear.app/docs/create-or-join-a-workspace  HTTP 404
notion.com/help/guests                 HTTP 404
```

**Everything OpenAI in this document came from search snippets, not from OpenAI's own
page.** That is the weakest evidence in the file. Verify it in a browser before
quoting a ChatGPT Business number to anyone.

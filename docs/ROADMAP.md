# Empyralis Roadmap

Living record of intent. Anything here is a decision, not code — nothing in
this file is built until it's referenced from an actual PR.

## Planned

### cli_subscription — BYO Claude Code / Codex via Gateway

**Status:** Planned, not built. Full architecture spec lives in
[`docs/CLI_SUBSCRIPTION_SPEC.md`](CLI_SUBSCRIPTION_SPEC.md).

**Motivation.** Empyralis serves three distinct intelligence sources, and a
user's answer to "who pays for the brain" is not a preference — it is what
they can afford and what they trust. All three must be first-class:

1. **Platform credits.** Empyralis-managed provider (DeepSeek today, others
   later). Simplest onboarding. The 3-minute promise. Paid via credit ledger.
2. **BYO API key.** The customer supplies a raw API key (Anthropic, OpenAI,
   etc.) stored in the workspace vault. The key never leaves the workspace
   boundary. Billed by the provider to the customer directly.
3. **BYO subscription on own hardware.** The customer has already paid for
   Claude Code Pro (or a Codex subscription). Empyralis Gateway, running on
   the customer's own machine, invokes the local `claude`/`codex` CLI with
   the customer's local login. Empyralis never touches the subscription
   token. This is the only legitimate way to use subscription pricing:
   the CLI runs on the user's hardware with the user's login.

**Why cli_subscription is the tricky one.** Modes 1 and 2 are pure cloud —
they work with just a workspace record. Mode 3 requires a live Gateway
capability, an active workspace binding, and turn-time routing that hands
off to the Gateway rather than to a cloud provider. Ledger events must
record `execution_tier: gateway_brain` so per-minute metering (Phase-Z) can
distinguish subscription turns from platform-credit turns.

**What's needed to build (in order — from CLI_SUBSCRIPTION_SPEC.md):**
- **G1** Gateway advertises `llm_runtime` capability (which CLI is
  installed, whether it's authenticated, plan tier).
- **G2** Control-plane provider resolver adds a `cli_subscription` mode:
  when set on `model_config`, resolve via Gateway rather than cloud.
- **G3** WSS message type `llm_generate` — control plane → Gateway.
- **G4** Gateway spawns CLI with the right args (`claude -p ... --output-format stream-json`
  or `codex exec ... --json`), streams response back chunked.
- **G5** Ledger `execution_tier: gateway_brain`, `runtime: claude_code|codex`,
  `gateway_id`.

**Deliberate non-goals:**
- No pooled/hosted subscription keys. That would be TOS-violating and
  wrong-shape.
- No fallback from `cli_subscription` to platform credits. If the Gateway
  is unreachable, the turn fails clearly; the user re-plans.

---

### Per-agent hosted bots — named specialists on the platform bot pool

**Status:** Planned, not built.

**Motivation.** Today "hosted Telegram" means one shared workspace bot
(`@EmpyralisSageBot`) that talks as Sage on behalf of the whole workspace.
This is right for onboarding: a new user pairs once and starts talking.

But specialist agents ("SupportBot", "SchedulingBot") should have their own
Telegram identity — separate handle, separate conversation history — without
the customer having to visit BotFather to create and configure a bot per
agent. The platform should host a pool of bots and assign one per named
specialist on demand.

**The three pieces to build:**

1. **Bot pool.** A platform-managed pool of Telegram bots (created and
   authenticated once, tokens held by Empyralis). Each pool entry has a
   handle, a token, and a status (`available` | `assigned` | `retired`).
2. **Per-agent binding.** When a customer creates a specialist agent, the
   agent creation flow claims one pool bot and stores a
   `hosted_bot_binding` on the agent record. The bot's webhook is
   updated to route by the binding rather than by workspace.
3. **Router.** The hosted webhook handler routes by `hosted_bot_binding →
   agent_id` before falling back to the workspace-Sage router. So the
   right agent replies without the customer configuring anything.

**Deliberate non-goals:**
- No customer-visible bot management UI in v1. The customer picks an agent
  name; the platform picks a bot handle; the customer gets a t.me link.
  Renaming and rebranding come later (or through BYO connectors).
- No cross-channel bot pool. Telegram first. Discord/WhatsApp when they
  each have their own equivalent.

---

## Phase-Z — Burst-to-hardware (deferred)

**Status:** deferred, not built.

**Motivation.** Cloud-only agents (hosted Telegram + platform credits + cloud
MCP tools) are the day-one value path — no hardware needed. But some agent
turns are cheap only if they run on hardware the customer already trusts:
long browser sessions, local-file edits, private-network reads. Instead of
forcing the customer to keep a VPS or a Mac Mini running 24/7, an agent turn
should be able to burst onto ephemeral hardware for the duration of that one
task and hand back a result.

**The three pieces to build (in order).**

1. **Per-task hardware flag.** A turn carries an explicit
   `runtime_target: cloud | burst_hardware | pinned_hardware` decision that
   Sage (or the calling agent) sets when planning the turn. Cloud stays the
   default. Burst is opt-in, per turn, with a reason the caller records so
   we can audit why cloud was insufficient. Pinned targets the customer's
   own long-running node when they have one.

2. **Ephemeral provision → run → deprovision.** When a turn asks for
   burst_hardware, the runtime provisions a VM (initially a preconfigured
   VPS image, later a firecracker/microVM) with the agent's tool sandbox
   preloaded, streams the turn through it, and tears it down when the turn
   or its supervising task completes. The customer never sees the machine;
   they see a bill line and an activity event.

3. **Per-minute metering.** Bursts bill in wall-clock minutes (round up to
   the nearest whole minute, minimum 1). The meter is authoritative on the
   provision → deprovision timestamps, not the agent's self-reported turn
   duration. Bills roll into the same credit ledger as platform_credits AI
   spend — one wallet, two line items (AI credits, burst minutes).

**Explicitly out of scope for Phase-Z:**
- Autoscaling pools of pre-warmed hardware (that's Phase-Z+1 when we know
  the burst rate).
- Multi-region routing (single region until we have a customer who needs
  otherwise).
- Customer-supplied hardware in the burst path (`pinned_hardware` covers
  that separately).

**Prerequisites (must ship first, before we start Phase-Z):**
- Cloud-only value path proven — a first-time user gets a real reply on
  hosted Telegram in under 3 minutes with zero hardware, ONE cloud MCP
  tool in use.
- Credit ledger + platform_credits mode debited on real Sage turns, with
  an activity event per turn. (Partially built today; needs verification
  on live traffic.)
- Sage decides when a turn needs burst — a planning-side capability that
  is not yet wired.

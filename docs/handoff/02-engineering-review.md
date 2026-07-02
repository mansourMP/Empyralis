# Prompt: Engineering Team Platform Audit & Strategy Review

This prompt is for a Claude Code session acting as an engineering review panel. You are reviewing the Empyralis platform — its architecture, its business concept, its codebase, and its viability. You need to produce honest, specific, actionable findings. No flattery. No sugar-coating.

## What Empyralis is (and what it's trying to be)

Empyralis is a managed AI agent platform. The pitch:

- Every user gets their own agent (Sage) that lives in the cloud.
- The agent connects to the user's SaaS tools (Gmail, Calendar, Slack, Notion, GitHub, etc.) via MCP + OAuth.
- The agent talks to the user through channels (Telegram, Discord, Slack, WhatsApp, etc.).
- For base tier (cloud-only): the agent uses MCP connectors + memory + chat. No shell, no browser. Enough for email/calendar/SaaS workflows.
- For hardware tier: the user installs a Gateway on their own Mac/Linux/Windows, or a dial-out worker on their own VPS. The agent gets shell + browser control through ONE remote-hands protocol.
- BYOK: bring your own API key (OpenAI, Anthropic, DeepSeek, etc.). Or use platform credits. Or use a local AI model. One road at a time, no silent fallback.
- Sub-agents: same Agent class as Sage, each with their own name, instructions, memory, channel bindings, and connector allowlist. Users can create business-specific agents (e.g. a customer-support agent with its own Telegram bot).

The platform is live at empyralis.ai (VPS at 165.227.25.201). It has real code, real OAuth integrations, real channels. But the owner is asking fundamental questions about whether it's the right thing and how to make it right.

## Context — read these before you start

### Codebase map
1. `docs/PLATFORM.md` — the current architecture document. Read Section 0 (current reality vs target) FIRST. It explains the two-backend situation.
2. If it exists, `docs/handoff/PLATFORM-MAP.md` — the exhaustive platform map produced by the graphify mapping prompt. This is your primary technical reference.
3. `docs/graphify-report.md` — auto-generated knowledge graph: 27,967 nodes, 71,974 edges, 1,127 communities. Import cycles, god objects, isolated nodes.

### Product shape and decisions (fetch these from Linear using MCP tools)
4. [PLATFORM OVERVIEW](https://linear.app/mansurao/document/platform-overview-empyralis-one-agent-one-service-many-configurations-86a18989075a) — the settled target shape. Phases, non-negotiables, tier split.
5. [Agent model](https://linear.app/mansurao/document/product-shape-agent-model-one-agent-class-user-facing-configurations-09aad1212721) — one Agent class, Sage + sub-agents.
6. [Channel families](https://linear.app/mansurao/document/product-shape-channel-families-cloud-channels-gateway-required-per-87f79a94df41) — 3 families, Gateway requirement for personal channels.
7. [Execution layers](https://linear.app/mansurao/document/product-shape-execution-layers-brain-hands-memory-8293a6c89488) — brain/hands/memory as independent dials.
8. [MCP integration](https://linear.app/mansurao/document/product-shape-mcp-integration-cloud-to-cloud-no-user-hardware-b8322eecf6d0)
9. [AI provider strategy](https://linear.app/mansurao/document/decision-ai-provider-strategy-platform-credits-byok-one-road-no-silent-2f13c73882e7)
10. [Agent simplification decision](https://linear.app/mansurao/document/decision-agent-simplification-synthesis-from-anthropic-openai-research-ce005e458c1e)

### Principles (non-negotiable — do not recommend violating these)
11. [No approval system](https://linear.app/mansurao/document/principle-no-approval-system-agent-acts-on-reasoning-d5e74f03bb43) — THE moat.
12. [Agent voice vs platform voice](https://linear.app/mansurao/document/principle-agent-voice-vs-platform-voice-pigeon-theory-9d3267985c85) — pigeon theory.
13. [No over-engineering](https://linear.app/mansurao/document/principle-no-over-engineering-the-approval-lesson-cc1f86ede171)

### Memory files (in `.claude/projects/-Users-mansur-empyralis/memory/`)
14. `empyralis-platform-vision.md` — north star
15. `v2-rebuild-2026.md` — why there are two backends
16. `channel-strategy.md` — channel families
17. `tool-model-shell-first.md` — tool philosophy
18. `safety-and-multiagent-shape.md` — blast radius, isolation
19. `verified-status-channels-hardware.md` — F1 ground-truth audit
20. `agent-as-worker-no-approval.md` — governance model
21. `agent-vs-platform-voice.md` — pigeon theory
22. `ai-credit-model-no-fallback.md` — credit model
23. `validate-with-one-user.md` — "cure for doubt is 1 real user"
24. `user-solo-founder-strain.md` — the owner's situation (solo founder, isolated abroad, ties self-worth to shipping)

## The owner's concerns — address ALL of these

The owner articulated these concerns. Don't skip any of them. If you think a concern is based on a wrong assumption, say so and explain why.

### Concern 1: Business viability — "why would anyone use this?"

The owner's words: "I mean why am I supposed to use this platform if Claude is already perfect... AI itself is not a business anymore what it provides is the real business."

Questions to answer:
- What does Empyralis provide that Claude Code, Codex, ChatGPT, or a direct API call to an LLM does NOT provide?
- Is "managed AI agents on the cloud" a real differentiator or is it a commodity wrapper around someone else's model?
- Who is the actual customer? (Developers? Non-developers? Businesses? Individuals?)
- What is the ONE workflow that would make someone come back the next day? Be specific. Not "productivity" — name the exact use case.
- Is this a platform business or a feature? Be honest.

### Concern 2: Architecture — "what to delete, what to add, what's wrong"

The owner's words: "this engineer team would help me to map out the entire platform and would help me what to delete and help me to decide what to delete what to add which one is wrong which one is right or I should build this instead of building this because it makes this thing fragile because we are not having one agent."

Using the platform map and graphify data, answer:
- What's the single biggest architectural fragility? (Hint: look at import cycles, god objects, and the "current vs target" gap.)
- What should be deleted immediately? (Dead code, dead routes, dead subsystems. Name specific files and directories.)
- What's the correct target architecture? Does the Linear PLATFORM OVERVIEW's "one Agent class, one router, one vault" vision make sense? If not, what SHOULD it be?
- The `server_modules/` (190 files, production) vs `server/` (skeletal, target) split — is the migration plan right? Should `server/` be the target? Or should we consolidate `server_modules/` in place?
- What's MISSING entirely? (Auth? Billing? Monitoring? Rate limiting? Something else?)

### Concern 3: Agent model — "one agent vs many agents"

The owner's words: "every single AI agent would have its own all of them are the same actually if I am connected my this Telegram account the other one would be able to work with it as well, but it would be enable and disable thing... the same architecture or the same service for example or the same gateway but different credentials or something like that."

Questions to answer:
- Does the "one Agent class, many configurations" model (from the Linear doc) actually work? Or is it aspirational?
- How should sub-agents share vs isolate: memory, channels, connectors, credentials, gateway?
- "Same gateway but different credentials" — is this the right model? One Gateway on user hardware, multiple agents routing through it with different auth?
- Should a sub-agent be able to have its own Telegram bot? Its own Discord bot? How does that work with ONE channel router?
- What's the right isolation boundary? (Per-agent memory? Per-agent OAuth scope? Per-agent credit pool?)

### Concern 4: Channels — "cloud bots vs personal accounts"

The owner's words: "first one is having a chatbot, where it wouldn't require any hardware and the only thing is this pigeon theory only delivering the message end to end... and the other one would be an agent having full control over the platform or this entire application it could be telegram or Discord and other things if it's if it needs gateway then it should do like this but if it needs something more like if if it doesn't need to getaway but it would have the same power than it should be it should be just on the cloud without hardware."

Questions to answer:
- Is the channel strategy right? Cloud bots (Telegram Bot API, Discord Interactions, Slack Events API) for no-hardware + Gateway (GramJS, Baileys, BlueBubbles) for personal accounts?
- The owner seems to want "cloud without hardware but same power as Gateway" — is that possible? For which channels? (Hint: Telegram Bot API can do most things GramJS can. Discord bot can't DM as a user.)
- The F1 audit found 3 proven channels (Telegram hosted, Discord DM, Slack) and multiple dead/skeleton ones. Which channels should be killed entirely? Which should be prioritized?
- WhatsApp is dead per channel strategy (Meta banned AI assistants Jan 2026). Confirm this is still true.
- The "12 files to add a channel" problem — what should the real number be? What's the right abstraction?

### Concern 5: UI — "reshape or kill?"

The owner's words: "Maybe we should just disable this user interface entirely user would just come inside the platform connect or create their agent that will be it or create their gateway that would be it or connect their hardware into this platform. I really think it should be like this but everything should be kind of like manage it."

And also: "I don't want to delete my user interface. I just want to reshape it maybe because right now it has the main agent and some thing like this it's looks pretty fragile and I don't think somebody would use it."

Questions to answer:
- The current UI (`legacy/frontend`, the "real" one with sidebar: Memory, Tasks, Library, AI setup, Connectors, Projects, Agents, Hardware) — is it the right surface? Or is it too complex?
- "User comes in, creates an agent, connects their gateway, done" — is this the right simplification? What does the minimal viable UI look like?
- Should the UI be a web dashboard? Or just chat interfaces (Telegram, Discord, web chat widget)? Or both?
- The `frontend/v2/` bare chat skeleton is supposed to be killed once legacy frontend is rewired. Is that the right call? Or should v2 be built up instead?
- What would make someone use this instead of just talking to Claude directly in claude.ai?

### Concern 6: Hardware — "Gateway and supervisor"

The owner's words: "leverage in Gateway supervisor and other things you know just AI on the cloud with one account."

Questions to answer:
- The Gateway (Node.js, WSS reverse tunnel) + Supervisor (Rust, uncompiled, policy kernel) architecture — is this the right split?
- The supervisor binary has NEVER been compiled (per F1 audit). Is it even needed? What does it provide that can't be done in the Gateway or the cloud control plane?
- "One remote-hands protocol with two install packages" (Gateway for Mac/Linux/Windows, dial-out worker for VPS) — is this the right abstraction? Or are these genuinely different things that shouldn't share a protocol?
- The Cloud Computer tier (proven: browser control on DigitalOcean droplet via Playwright, 150 green tests) — should this be prioritized over user Gateway? It's "managed" — no user hardware needed, platform provisions the droplet.
- Is hardware even the right priority? Or should the platform nail cloud-only first (MCP connectors + channels + memory) and defer hardware entirely?

### Concern 7: Memory — "everything should be isolated"

The owner's words: "the memory and others everything kind of like should be isolated you know and agent still would be able to kind of like connect to chat bot like global chatbots... or discord you know."

Questions to answer:
- Is the three-tier memory model (config/outputs/private per agent) right? Does it map to what's actually in the code?
- File-based memory (the Anthropic SKILL.md/CLAUDE.md pattern) vs database — is file-based right for production?
- Sage can see sub-agent config and outputs but NOT private reasoning. Is this the right boundary?
- What about shared memory? Should two sub-agents ever share context? (e.g. a customer-support agent and a sales agent both need to know the customer's history.)

### Concern 8: Everything is possible — "BYOK, local AI, subscriptions"

The owner's words: "if you want coding you want it's possible if you want to use your own API, it's possible if you want to use your own local AI model it's possible if you want to work with your AI subscription it's possible like everything is just possible."

Questions to answer:
- Is "everything is possible" the right strategy? Or is it a trap that makes nothing work well?
- What's the ONE path that should work perfectly first? (Platform credits? BYOK? Local AI?)
- The credit model: 10k free credits per user, covers entire service. Is this sustainable? What's the actual cost per turn?
- Subscription is deferred until international payments exist (owner is based in China, can't use Stripe). What's the interim monetization? Credits only? Free-only until payments work?

### Concern 9: Security — "cyber security and other things that I should be worried about"

Questions to answer:
- What are the top 5 security concerns in this architecture? Be specific. Cite files and attack vectors.
- The OAuth token vault — how are tokens stored? Encrypted at rest? Who holds the key?
- The Gateway WSS tunnel — what's the auth model? Can a compromised Gateway escalate to other users?
- MCP connectors — what's the blast radius if a connector's OAuth token leaks?
- Shell access (hardware tier) — what confinement exists? The Rust supervisor was supposed to handle this but it's uncompiled.
- Multi-tenancy — can User A access User B's workspace, memory, or credentials? What enforces the boundary?
- The platform has 75 known violations catalogued in PLATFORM.md (31 "I"/"my" string leaks, 20 "your"/"you've" leaks, 10 channel-logic-bleeding-into-control-plane, 6 gateway-logic-misplaced, 8 structural). Which of these are actual security concerns vs just code-quality issues?

### Concern 10: The path forward — "what am I supposed to do?"

The owner is a solo founder, isolated abroad, tying self-worth to shipping. He's been building for ~3 years. Parts of the platform work. Parts are skeleton. The architecture has drift between what's deployed and what's declared as target. He's tired and asking whether to continue.

Answer honestly:
- Is this platform worth continuing? Or is it a learning project that should be reframed as such?
- If worth continuing: what's the ONE thing to focus on for the next phase? (Not 7 phases. ONE.)
- What should be explicitly killed/deprecated/deleted? Name names. The owner has veto but wants to hear the case.
- If NOT worth continuing in current form: what's the salvageable piece? (The OAuth vault? The MCP integration? The Gateway? The channel router?)
- "Validate with one user" — who is that one user? What's the simplest possible workflow to put in front of them?
- What questions should the owner be asking that he ISN'T asking?

## Output format

Write your findings to `docs/handoff/ENGINEERING-REVIEW.md`.

Structure it as:

```
# Empyralis — Engineering Review

## 1. Is this a business?

[Honest assessment. What's the differentiator? Who's the customer? Why would they use this instead of Claude Code / ChatGPT / a direct API call?]

## 2. Architecture health

[What's broken, what's fragile, what should be deleted, what should be the target. Specific files and subsystems.]

## 3. Agent model — right or wrong?

[One Agent class, many configurations. Sub-agent isolation. Channel bindings. Memory scoping.]

## 4. Channel strategy assessment

[What works, what's dead, what should be killed, what should be prioritized. The 12-file problem.]

## 5. UI — what should it be?

[Web dashboard? Chat-only? Both? The legacy frontend vs v2 question.]

## 6. Hardware — Gateway, Supervisor, Cloud Computer

[What's proven, what's skeleton, what's needed, what's not.]

## 7. Memory model

[Three-tier file-based — right approach? Isolation boundaries. Shared context question.]

## 8. AI provider strategy

[Credits vs BYOK vs local vs subscription. What's the ONE path to make perfect first.]

## 9. Security review

[Top 5 concerns. Token vault. Gateway auth. MCP blast radius. Shell confinement. Multi-tenancy.]

## 10. The path forward

[Continue or reframe? If continue: ONE focus. What to kill. Who's the first user. What questions isn't the owner asking.]

## Appendix A: Delete list

[Every file, directory, subsystem, or feature that should be deleted or deprecated. With reasons. The owner has veto — make the case, don't pre-empt the veto.]

## Appendix B: Build list

[What's MISSING that must be built. Ordered by dependency, not by schedule. No timelines.]

## Appendix C: Open questions

[What we can't answer without more information. What the owner needs to decide that this review can't decide for him.]
```

## Rules

1. **No flattery.** The owner explicitly asked for direct, harsh feedback. Do not say "this is impressive" or "you've built a lot." Say what's wrong and what to do about it.

2. **Specific, not general.** Don't say "improve error handling" — say "`sage_agent_runtime_service.py:505` returns a hardcoded string when MCP invocation fails; it should return a structured error the frontend can render."

3. **Name what to delete.** Every recommendation to remove something must name the specific file, directory, or feature. The owner has veto — he'll use it if he disagrees. Don't pre-empt the veto by being vague.

4. **Distinguish "broken" from "wrong design."** A bug (tool dedup dropping all tools) is different from an architectural choice you disagree with (file-based memory vs database). Be clear which is which.

5. **Respect the non-negotiables.** Do not recommend adding an approval system. Do not recommend making channels carry logic. Do not recommend silent fallback to cheaper models. These are settled.

6. **If you think a non-negotiable is wrong, say so explicitly.** "I understand the no-approval principle, but here's why I think it's wrong for [specific scenario] and what I'd recommend instead." The owner can override his own rules.

7. **This is a review, not a build plan.** You're telling the owner what's right and wrong with the platform. You're NOT writing a 6-week sprint plan. No timelines, no deadlines, no "Phase 1-7."

8. **Read the code.** Don't review from the docs alone. Verify claims against actual files.

# Market Reference

> **Stale channel assumptions — read before using this doc.**
> This research was prepared 2026-04-18 and describes WhatsApp as a key channel.
> **WhatsApp is no longer a supported Empyralis channel.** Personal WhatsApp (Baileys) is banned
> by the provider. WhatsApp Business API bars third-party general-purpose AI assistants as of
> Jan 15, 2026. Do not use this doc's WhatsApp framing in any customer-facing material.
> The recommended reliable Telegram path is the hosted bot, not WhatsApp.

Prepared: 2026-04-18

Scope: competitive research — buyer alternatives for Telegram chatbots, real small-business AI deployments, and a realistic first paying-customer wedge for Empyralis. WhatsApp sections are now stale; see note above.

## 1. What Businesses Use Today for Telegram and WhatsApp Chatbots

### Market read

Most small and mid-sized businesses do not buy a "vertical AI deployment platform" first.

They usually buy one of these instead:

1. A shared inbox plus automation tool.
2. A WhatsApp BSP plus template messaging and flows.
3. A CRM with messaging attached.
4. A no-code bot builder that is good enough for lead capture and FAQs.

That means Empyralis is not competing first against "AI labs." It is competing against message ops tools that are easier to buy, easier to onboard, and already connected to WhatsApp.

### Top 5 competitors businesses are using now

| Competitor | Channel fit | Official pricing | Biggest limitation |
| --- | --- | --- | --- |
| [respond.io](https://respond.io/pricing) | Strong for WhatsApp and Telegram in one team inbox | Starter `$79/mo`, Growth `$159/mo`, Advanced `$279/mo`, Enterprise custom. WhatsApp fees are extra. | Great for inbox + routing + automation, but still mainly an omnichannel ops tool. It is not opinionated vertical AI, and the pricing climbs with contacts, users, and added WhatsApp usage. |
| [Manychat](https://help.manychat.com/hc/en-us/articles/25800228332572-Pro-plan) | Strong for marketing-led Telegram + WhatsApp automation | Current help-center pricing for new accounts: Pro `$39/mo` monthly or `$29/mo` annual, includes up to 2,500 active contacts; additional inbox seats cost extra. WhatsApp template fees are extra. | Excellent for growth funnels and lead capture. Weaker for serious service operations, deeper back-office workflows, and vertical system integrations. |
| [SendPulse](https://sendpulse.com/pricing/messengers) | Broad SMB option for Telegram + WhatsApp + other messaging channels | Free tier available. Pro starts at `$12/mo` monthly or `$9.60/mo` annual. Official WhatsApp BSP pricing is layered on top by message type and country. | Attractive entry price, but it is still a generic flow builder. As workflows get more operational, data-heavy, or exception-prone, teams outgrow it fast. |
| [Kommo](https://www.kommo.com/pricing) | CRM-first messaging stack with WhatsApp and Telegram integrations | Base `$15/user/mo`, Advanced `$25/user/mo`, Pro `$45/user/mo`, Enterprise custom. AI agent usage is separate from seat pricing. | Strong if the buyer already thinks in CRM pipelines. Weak if they want a true customer-facing AI worker instead of a sales CRM with automation attached. |
| [WATI](https://www.wati.io/en/pricing/) | WhatsApp-first SMB platform | Growth / Pro / Business plans plus message charges; plan page shows paid tiers with 5 users included and extra user charges, and messaging is billed separately by message type and region. | Strong WhatsApp distribution and onboarding. Weak for Telegram, weak for cross-channel orchestration, and still mainly a WhatsApp operations layer rather than a general-purpose vertical AI system. |

### What these competitors all have in common

- They win because onboarding is clear.
- They sell a direct painkiller: "centralize messages," "automate replies," "run campaigns," "book faster."
- They usually stop at automation, inbox routing, templated replies, and basic integrations.
- They do not make the business feel like it bought a durable customer-facing AI operator.

### Where they are weakest

- Hard to connect messy business data into a stable assistant.
- Hard to govern what the bot is allowed to do.
- Weak memory, weak handoff logic, or weak vertical fit.
- Good for campaigns and support deflection, weaker for ongoing operational workflows.

## 2. What Vertical AI Deployment Looks Like for a Small Business

### Pattern

A real small-business deployment is usually narrower than the pitch deck.

It typically looks like:

1. One channel customers already use.
2. One or two high-frequency workflows.
3. One human fallback path.
4. One connected system of record.

If it tries to do too much too early, reliability drops and staff stop trusting it.

### Example A: Retail / e-commerce

Real example: [Modern Gifts via Interakt](https://www.interakt.shop/case-study/modern-gifts/)

| Area | What is connected | What questions it answers | What breaks |
| --- | --- | --- | --- |
| Retail gifting / e-commerce | Instagram and WhatsApp for customer conversations, Shopify for backend order management | Order updates, product discovery, customization requests, support questions, purchase nudges | The main break before automation was operational overload: missed messages, slow response times, and chat volume outgrowing the team. Even after deployment, product-specific edge cases and custom orders still need human escalation. |

What this shows:

- The real value is not "AI magic."
- The value is turning customer conversations into a repeatable revenue channel without losing messages.
- The data layer is usually catalog + order status + support history, not a giant enterprise stack.

### Example B: Medical / hospital operations

Real example: [Surbo hospital appointment WhatsApp case study](https://surbo.io/blog/case-study-hospital-appointment-on-whatsapp.php)

| Area | What is connected | What questions it answers | What breaks |
| --- | --- | --- | --- |
| Hospital / appointment management | Official WhatsApp account, hospital information system (HIS), appointment scheduling logic, conversational records | Book appointment, reschedule, cancel, choose doctor, choose procedure, send reminders and confirmations | Clinical edge cases still require a human. Anything ambiguous, urgent, or compliance-sensitive must escalate. The system reduces front-desk load, but it does not replace staff judgment. |

What this shows:

- Medical works best when the AI does operations, not diagnosis.
- The winning use case is scheduling, reminders, triage, and navigation.
- The failure mode is trying to answer questions that need clinical judgment, consent handling, or nuance.

### Example C: Service / hospitality business

Real example: [Zostel via WATI](https://www.wati.io/case-studies/zostel/)

| Area | What is connected | What questions it answers | What breaks |
| --- | --- | --- | --- |
| Hospitality / travel operations | Booking confirmations, reminders, payment links, guest messaging workflows, segmentation and campaign flows | Booking confirmation, reminders, payment prompts, basic stay queries, campaign follow-up | Their in-house system hit scale limits and required recurring engineering work. Even after moving to WATI, complex guest issues still need human handling and workflow quality depends on clean property and booking data. |

What this shows:

- Small service businesses buy automation to get rid of repetitive, time-sensitive coordination.
- The most valuable answers are operational: "When is my booking?" "How do I check in?" "Where is my payment link?" "Can I reschedule?"
- The system fails when the source data is stale or when exceptions are frequent.

### Common deployment shape across all 3 examples

| Common element | What it usually is |
| --- | --- |
| Primary channel | WhatsApp first, sometimes Instagram or Telegram as acquisition / secondary channel |
| Source of truth | Shopify, booking system, calendar, HIS, CRM, or spreadsheet-backed workflow |
| Most common intents | Order status, booking, reminders, lead qualification, FAQ, payment follow-up |
| Human fallback | Front desk, sales agent, support rep, clinic staff, property manager |
| Main breakage mode | Bad data, missing integrations, exceptions, channel policy limits, or workflows that try to act too broadly |

## 3. Current State of WhatsApp Business API Access

### Short answer

It is easier than it used to be, but it is still not "instant and done."

The fastest route today is through a BSP or tech provider with embedded or self-serve signup. The slowest route is trying to understand and wire the whole Meta path from scratch when you are a small business with weak documentation, no clear website, or inconsistent business identity.

### What is true right now

- WhatsApp pricing changed from conversation-based pricing to per-template-message pricing in 2025, and current BSP documentation reflects that new model.
- A business still needs a valid Meta business setup, a usable phone number, and a compliant display name.
- Faster onboarding exists now through providers like Twilio.

### How hard is approval?

For a normal small business with:

- a working website,
- a real legal identity,
- consistent business name,
- a phone number it controls,
- and a clear use case,

it is moderate, not impossible.

For a brand-new business with:

- no real website,
- poor business documentation,
- mismatched brand names,
- or policy-risk messaging,

it becomes slow and annoying very quickly.

### What still causes friction

| Friction point | Why it matters |
| --- | --- |
| Meta Business verification | Businesses with weak documentation or inconsistent identity slow down here. |
| Display name review | If the display name does not meet Meta rules, sending limits can stay low or the sender can be rejected. |
| Existing phone number reuse | A number already tied to WhatsApp or WhatsApp Business often has to be detached first. |
| Template approval | If the outbound messaging plan depends on marketing templates, approval quality matters immediately. |
| Country-level cost variance | Message cost is not fixed globally; it changes by destination and message category. |

### What the costs actually look like

There are usually two layers of cost:

1. Platform or BSP cost.
2. Meta messaging cost.

Examples from current official vendor docs:

- [Twilio WhatsApp pricing](https://www.twilio.com/en-us/whatsapp/pricing): Twilio charges `$0.005` per WhatsApp message inbound or outbound, and passes through Meta template fees on top.
- [360dialog pricing](https://docs.360dialog.com/docs/get-started/pricing): standard monthly channel fee is listed at `$59/mo` for the regular channel tier, plus Meta messaging fees.
- [SendPulse pricing](https://sendpulse.com/pricing/messengers): Pro starts at `$12/mo`, and WhatsApp template pricing is layered separately by destination country and template type.
- [WATI pricing](https://www.wati.io/en/pricing/): recurring platform subscription plus message charges based on region and message type.

### Is there a faster path for small businesses?

Yes.

The fastest path today is not "becoming a WhatsApp platform expert." It is using embedded or self-serve onboarding through a provider.

The clearest current example is Twilio:

- [Twilio Self Sign-Up is live for all direct customers](https://www.twilio.com/en-us/changelog/whatsapp-self-sign-up-now-available-to-all-direct-customers)
- [Twilio docs say direct customers can register senders through self-sign-up](https://www.twilio.com/docs/whatsapp/self-sign-up)
- [Twilio help states self-sign-up is the faster path, but businesses stay restricted until Meta business verification is complete](https://help.twilio.com/articles/6686317584155-What-is-WhatsApp-Self-Sign-Up-)

Important practical constraint:

- Faster signup does not remove Meta policy and verification requirements.
- It mainly removes the old manual waiting and partner back-and-forth at the start.

### Best practical read for Empyralis

If Empyralis wants small businesses in the next 30 days, the product should assume:

- they do not want to become WhatsApp infrastructure experts,
- they do not want to compare Meta rate cards by hand,
- and they will strongly prefer "click, connect, verify, send" over a platform that needs a week of setup calls.

## 4. Realistic First Paying Customer for Empyralis in the Next 30 Days

### The wrong first customer

Do not optimize for:

- hospitals first,
- large retail first,
- or broad "AI for any business" positioning.

Those buyers need more trust, more integrations, more compliance, and more implementation polish than Empyralis should promise in the next 30 days.

### The right first customer profile

The best near-term customer is:

- a 5 to 30 person service business,
- already using WhatsApp or Telegram as a real customer front door,
- currently handling leads and support manually,
- and already feeling inbox pain.

Good examples:

- aesthetic clinic or dental clinic for appointment triage,
- travel or hospitality operator for booking and reminders,
- home services business for lead qualification and scheduling,
- premium retail showroom with high message volume and repeat questions.

### Best first use case

The best first use case is not "general AI assistant."

It is:

**customer-facing messaging operator for lead qualification, booking, reminders, and human handoff**

That means Empyralis should own:

- first response,
- structured qualification,
- FAQs,
- booking or scheduling intake,
- reminders,
- escalation to human.

Not:

- fully autonomous end-to-end operations across every system,
- open-ended support with no guardrails,
- or complicated cross-department orchestration on day one.

### What data this first customer would connect

Minimum viable data stack:

- business hours
- service catalog
- staff list or provider list
- appointment availability or request intake
- FAQs
- customer contact data
- follow-up status

That can live in:

- Google Sheets,
- a booking calendar,
- a basic CRM,
- or a lightweight booking system.

This is important: a real first customer does not need ten integrations. They need one or two clean systems and one reliable messaging workflow.

### Realistic price point

For the next 30 days, the cleanest pricing motion is:

| Offer shape | Realistic price |
| --- | --- |
| Setup + pilot | `$500` to `$2,000` one-time |
| Monthly software + support | `$200` to `$800/mo` |
| Done-for-you managed deployment | `$1,000` to `$3,000/mo` if it includes implementation and operator support |

If Empyralis sells only "software platform" too early, it competes directly with cheaper inbox tools.

If it sells:

- deployment,
- workflow design,
- and a live vertical specialist,

it can charge more and avoid being compared only on seats and message volume.

### What would make them switch from current tools

They will switch if Empyralis gives them all 4 of these:

1. Faster setup than stitching together Twilio + CRM + chatbot builder.
2. Better business fit than a generic inbox tool.
3. Lower operator burden than manual WhatsApp handling.
4. More control and reliability than a "magic AI bot" that cannot be governed.

Concretely, the switching triggers are:

- too many leads/messages slipping through the cracks,
- team spending too much time answering the same questions,
- shared inbox tools feeling like glorified chat routing,
- current bot being too shallow,
- current CRM automation feeling too sales-centric and not operational enough.

### Strongest 30-day wedge for Empyralis

**Telegram/WhatsApp specialist for one narrow workflow in one service business**

Best candidate message:

> We deploy a customer-facing messaging specialist that qualifies leads, answers routine questions, books or routes requests, remembers context, and hands off cleanly to staff.

That is easier to buy than:

> We are a multi-agent governed operations platform.

### What Empyralis must prove to win that first customer

In the next 30 days, the product does not need to prove everything.

It needs to prove 6 things:

1. Connect a provider fast.
2. Connect WhatsApp or Telegram without drama.
3. Give the business one working customer-facing agent.
4. Answer repeat questions reliably from connected business data.
5. Escalate to a human cleanly.
6. Show enough logs, control, and memory that the owner trusts it.

If those 6 things are real, Empyralis has a sellable wedge.

If not, the market will compare it to cheaper chat automation tools and ask why it is harder to use.

## Bottom Line

The market is crowded with inbox tools, BSPs, and no-code automations. The gap is not "more channels" or "more AI wording." The gap is a deployable vertical messaging worker that is:

- fast to onboard,
- opinionated around one business workflow,
- trustworthy,
- and easier to run than assembling 4 separate tools.

That is the most realistic path to a first paying customer in the next 30 days.

## Primary Sources

- [respond.io pricing](https://respond.io/pricing)
- [Manychat Pro plan](https://help.manychat.com/hc/en-us/articles/25800228332572-Pro-plan)
- [Manychat WhatsApp pricing guide](https://help.manychat.com/hc/en-us/articles/14281380243740-WhatsApp-pricing-guide)
- [SendPulse chatbot pricing](https://sendpulse.com/pricing/messengers)
- [Kommo pricing](https://www.kommo.com/pricing)
- [WATI pricing](https://www.wati.io/en/pricing/)
- [Interakt Modern Gifts case study](https://www.interakt.shop/case-study/modern-gifts/)
- [Surbo hospital WhatsApp case study](https://surbo.io/blog/case-study-hospital-appointment-on-whatsapp.php)
- [WATI Zostel case study](https://www.wati.io/case-studies/zostel/)
- [Twilio WhatsApp pricing](https://www.twilio.com/en-us/whatsapp/pricing)
- [Twilio WhatsApp Self Sign-up announcement](https://www.twilio.com/en-us/changelog/whatsapp-self-sign-up-now-available-to-all-direct-customers)
- [Twilio WhatsApp self-sign-up docs](https://www.twilio.com/docs/whatsapp/self-sign-up)
- [Twilio help: What is WhatsApp Self Sign-Up?](https://help.twilio.com/articles/6686317584155-What-is-WhatsApp-Self-Sign-Up-)
- [360dialog pricing](https://docs.360dialog.com/docs/get-started/pricing)

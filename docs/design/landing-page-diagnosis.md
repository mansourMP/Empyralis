# Landing page diagnosis — why it reads "flat"

Status: diagnosis + proposal only. No product code changed. The landing page
itself (`frontend/lib/marketing/landing-page.tsx` + `landing.css`) was not
edited to produce this document.

**Method**: started the real dev server (`.claude/launch.json`'s `web` config,
`cd frontend && next dev -p 3000`, `NEXT_DIST_DIR=.next-preview-b`) with no
backend running. `frontend/app/page.tsx:17-23` degrades gracefully when
`/api/auth/account-shell` 503s (confirmed in server logs) and renders
`<LandingPage />` directly — i.e. the exact anonymous-visitor view. Captured
real screenshots via the Browser pane at:
- Desktop, full page: 1280×3000 viewport (no scroll needed, avoids a capture
  flake in this environment — see note below), light theme (the resolved
  default for an anonymous visit).
- Mobile, full page: 375×3050 viewport, light theme.
- Desktop, full page, dark theme: forced via `document.documentElement.
  dataset.theme = 'dark'` after confirming (see §4) the page does not
  self-select dark even when the OS/browser reports
  `prefers-color-scheme: dark`.

(Aside on method: the Browser pane's `scroll` action repeatedly produced a
blank/stuck frame in this session regardless of page — confirmed via
`elementFromPoint`/`getComputedStyle` that the real DOM was intact and
correctly scrolled underneath a stale paint. Switched to full-height
viewports instead of scrolling, which reproduced cleanly every time. Noting
this so it isn't mistaken for a rendering bug in the product itself — it
wasn't; it was this session's screenshot tool.)

---

## 1. What's actually on the page (for reference)

`frontend/lib/marketing/landing-page.tsx:259-272` — the full section list,
top to bottom:

1. Nav (`LandingNav`, :80-105) — wordmark + "Log in" + "Get started"
2. Hero (`LandingHero`, :107-126) — kicker, H1, subtitle, one CTA button
3. "Why it exists" (`WhyItExists`, :128-141) — centered heading + one paragraph
4. "How it works" (`HowItWorks`, :143-162) — 3-step numbered vertical timeline
5. "What you get" (`Pillars`, :164-184) — 4-row flat divided list, tiny icons
6. "What ships with every agent" (`Capabilities`, :186-220) — 3 columns of
   logo rows (channels / model providers / apps)
7. "Why now" (`WhyNow`, :222-234) — centered heading + one paragraph
8. Footer CTA + links (`LandingFooter`, :236-257)

Every section is `text + optional tiny icon`. There is no image, no product
screenshot, no illustration, no chart, and no non-text visual element bigger
than a 24px logo anywhere on the page.

---

## 2. Top 3 causes of flatness (verified, not guessed)

### Cause 1 — Zero background/depth variation across ~3000px of page

Every single section — hero, "why it exists," "how it works," "what you
get," "what ships with every agent," "why now," footer — renders on the
exact same flat `var(--app-bg-page)` (`#ffffff` in light,
`theme-tokens.css:145`; `#0d0d0f` in dark, `theme-tokens.css:79`). The
**only** visual separator between eight consecutive sections is a 1px
`border-top: 1px solid var(--app-border-subtle)` (`landing.css:139`,
reused verbatim by `.landing-section`). Confirmed directly in both
full-page screenshots (light and dark): scrolling from the hero to the
footer, the background color literally never changes — you can only tell a
new section began because the text restarts and a hairline appears above
it.

There is no card surface, no tinted band, no gradient, no `--bg-inset` /
`--bg-surface-2` fill anywhere in `landing.css` — those neutral "elevation"
tokens exist in the shared token file (`theme-tokens.css:82-85`) and are
used throughout the product chrome (fleet UI), but `landing.css` never
reaches for them. A page that is *only* hairlines on a single flat plane
for its entire length is the single biggest driver of "flat" — there is
nothing for the eye to use as a depth cue.

### Cause 2 — No visual proof of the product anywhere on the page

Grepped `frontend/public/brand-assets/` (the only images the landing page
imports from, per `landing-page.tsx:32-60`) top to bottom: it contains only
third-party brand/logo marks (channels, model providers, connected apps)
and the Empyralis hex mark. **There is no product screenshot, UI mockup,
chat transcript, terminal capture, or illustration anywhere in the
repository's public assets that the landing page could show.** This isn't
a placement mistake in the JSX — the asset doesn't exist yet to place.

For a company whose entire pitch is "agents that live in real channels and
act across your tools," the page never once *shows* an agent doing that.
Linear's own homepage (cited in the founder's north-star) leads with a
large, real, interactive-looking product mockup directly under the hero —
sidebar, an open issue, an agent panel — before a single word of "how it
works" copy. Ours has 3000px of prose and logo rows and never shows the
product. This is a structural gap, not a styling one: fixing it requires a
real screenshot/mockup asset, not a CSS change.

### Cause 3 — Typography and hairlines are the only design tool in use; everything else is deliberately suppressed

This one is subtler and is a direct, correct consequence of the founder's
own accent-rarity ruling — but it means *every other source of visual
interest* was also stripped out along with the purple, leaving nothing to
replace it:

- Accent color (`--app-accent`, `oklch(57% 0.155 305)` light /
  `oklch(64% 0.17 305)` dark, `theme-tokens.css:108,170`) appears in exactly
  two places on the whole page: the "BUILD YOUR EMPIRE" kicker text
  (`landing.css:97`) and the two CTA buttons (`.landing-cta--primary`,
  `landing.css:125-133`). That's correct per the ruling — but it also means
  a visitor's eye has only three small anchor points across the entire
  page.
- Motion: zero. No scroll-reveal, no stagger, no hover transform beyond a
  1px logo nudge (`landing-logo-row__logo:hover`, `landing.css:345-348`)
  and a color transition on the nav login link. The founder's own ruling
  allows "restrained motion" (Linear uses plenty of it); this page uses
  none.
- Icons: the `Pillars` section's icons are 18px lucide glyphs in
  `var(--text-tertiary)` (`landing.css:259-266`) — deliberately
  underweighted (correctly avoiding the banned "icon chip in a card" SaaS
  pattern, `landing-pillar` has no card/border/background at all,
  `landing.css:251-257`), but the net effect is they're nearly invisible
  next to 700-weight headings elsewhere on the page.
- Section rhythm *is* actually varied (centered prose sections alternate
  with left-aligned list sections — hero/why-it-exists/why-now are
  centered, how-it-works/what-you-get/capabilities are left-aligned) — this
  part is not a real problem and shouldn't be "fixed," it's evidence the
  layout logic is already reasonably considered. It's just not enough on
  its own without *any* other depth cue.

Net effect: the restraint that correctly avoids "purple everywhere" and
"generic SaaS card grids" was applied without adding back any of the
*allowed* alternatives (neutral elevation, a real product image, motion) —
so what's left reads as an empty draft rather than a restrained design.

---

## 3. Secondary/tangential findings (worth fixing, not the core "flat" complaint)

- **Two broken images on every load.** `landing-page.tsx:57-58` references
  `/brand-assets/apps/stripe.ico` and `/brand-assets/apps/salesforce.ico`.
  Both 404 (confirmed via network log: `GET .../stripe.ico → 404 Not
  Found`, `GET .../salesforce.ico → 404 Not Found`). The real files sitting
  right next to them are `frontend/public/brand-assets/apps/stripe.svg` and
  `.../salesforce.svg` — full-color SVGs already exist, the code just points
  at the wrong (non-existent) extension. Two silently-broken logos in the
  "what ships with every agent" trust row is a bad look for a YC-facing
  page and is a one-line-per-logo fix.
- **The page appears to ignore the visitor's OS dark-mode preference.**
  Confirmed live: with the browser's `prefers-color-scheme` forced to
  `dark`, `window.matchMedia('(prefers-color-scheme: dark)').matches`
  returned `true` but `document.documentElement.dataset.theme` still
  resolved to `"light"` for the anonymous landing view. Likely
  `AppThemeProvider`'s `preference` prop (`frontend/lib/ui/app-theme.tsx:49-67`)
  is being passed a hard `'light'` rather than `'system'` for logged-out
  visitors, rather than an issue in `landing.css` itself. Flagged for
  awareness; not investigated further as it's outside this task's scope
  (visual flatness), and is a behavior question (should anonymous marketing
  default to system theme?) more than a design one.

---

## 4. Ranked proposals

Ranked by (visual impact) × (small, safe, ruling-compliant change). None of
these were implemented — this is the proposal list only.

### 1. Add one real product visual directly under the hero
**What:** A single real screenshot or a lightly-chromed mockup of the
actual product — an agent conversation in a channel (e.g. a Telegram/
WhatsApp thread) or the fleet console's agent-detail view — placed right
after the hero CTA, before "Why it exists."
**Why it fixes flatness:** This is Cause 2, and the single highest-leverage
fix on the list — it's the one thing Linear's own homepage leads with and
we have nothing of. It also does double duty: it's the fastest way to make
"not a chatbot builder" legible to a YC reader without more copy.
**Ruling constraint:** Must be a real captured screenshot (or a faithful
recreation of real chrome), never a generic stock-style illustration or an
"icon in a gradient blob" — those are exactly the SaaS clichés the founder
banned. Keep it inside the normal centered container, not full-bleed.
**Cost:** Needs a real asset (a screenshot of the actual product), which
doesn't exist yet — this is the one item on the list that isn't pure CSS/
copy.

### 2. Give sections alternating neutral elevation, not just hairlines
**What:** Alternate `--app-bg-page` with `--app-bg-inset` /
`--app-bg-surface-2` (both already defined, unused by `landing.css`) as the
background for every other section — e.g. hero and "why it exists" stay on
page background, "how it works" sits on a whisper-toned inset band, "what
you get" back to page background, "capabilities" on inset again.
**Why it fixes flatness:** Directly answers Cause 1. This is exactly the
"faint neutral background" move the founder's own accent-rules memo
explicitly allows as the non-accent way to add depth — zero new colors,
reuses tokens already in the shared system.
**Ruling constraint:** Neutral only — never accent-tinted bands. Keep the
contrast subtle (these tokens are already whisper-quiet, e.g.
`--bg-inset: #101012` vs `--bg-page: #0d0d0f` in dark).
**Cost:** Trivial — a background-color line per section, using tokens that
already exist.

### 3. Fix the two broken logos (stripe.ico/salesforce.ico → .svg)
**What:** Change `landing-page.tsx:57-58` to point at the existing
`stripe.svg` / `salesforce.svg` files.
**Why it fixes flatness:** Doesn't fix flatness directly, but it's a
one-line, zero-risk credibility fix sitting in the exact section (trust/
capability logos) doing the most work to establish the product is real and
integrated — worth bundling with any pass through this file.
**Ruling constraint:** None — pure bugfix.
**Cost:** Two-line change.

### 4. Add restrained scroll-reveal motion to section entrances
**What:** A small, consistent fade/translate-up (e.g. 8-12px, 200-250ms,
triggered once on scroll-into-view) on each section's heading + body as it
enters the viewport. `motion` is already a dependency
(`frontend/package.json:14`, `gsap`/`lenis`/`motion` all present) — this
isn't a new library, just an unused one.
**Why it fixes flatness:** Directly answers the "no motion" half of Cause
3. Linear's own site leans on scroll-triggered reveals throughout; a static
page with 3000px of identical hairline sections reads inert by comparison.
**Ruling constraint:** "Restrained motion" per the north-star — subtle,
short, no bounce/spring theatrics, and must not delay the hero (hero should
render instantly, not fade in on load).
**Cost:** Small — one shared hook/wrapper component, applied per section.

### 5. Give the hero a single, quiet depth cue instead of pure flat white/black
**What:** A very subtle radial or linear gradient wash behind the hero
text only (e.g. a soft vignette from `--bg-page` to a whisper of
`--bg-inset`, or a faint top-down fade), contained to the hero section.
**Why it fixes flatness:** The hero is the single highest-attention area on
the page and is currently 100% flat. A near-invisible gradient (the kind
Linear itself uses under its own hero) reads as "considered" without
introducing any new hue.
**Ruling constraint:** Must stay strictly neutral/monochrome (grayscale
gradient only) — this is not a place to introduce the accent as a glow or
background tint; the accent stays confined to the kicker text and CTA.
**Cost:** Small — a background-image gradient on `.landing-hero`.

### 6. Strengthen the "What you get" (Pillars) icons or drop them
**What:** Either size the 4 lucide icons up slightly and give them a
touch more visual weight (e.g. `var(--text-secondary)` instead of
`var(--text-tertiary)`, `landing.css:266`), or remove them entirely and let
the list be pure text — currently they're too faint to register as a
design element but too present to ignore.
**Why it fixes flatness:** Minor, but this section currently has the
least visual identity of the whole page (a plain divided list with
barely-visible icons) — either committing to the icons or cutting them
resolves the "half-there" feeling.
**Ruling constraint:** Must not become the banned icon-chip-in-a-card
pattern — the existing flat-divided-list structure (no card, no background,
no border-radius) is already correct and should stay; only the icon weight
is in question.
**Cost:** Trivial — a color-token swap, or a JSX deletion.

### 7. Add a one-line, concrete proof point near the hero (numbers, not adjectives)
**What:** A small, quiet strip under the hero CTA or above the footer CTA
with something concretely true and specific (e.g. channel count, "runs on
your own hardware or ours," a real usage/uptime figure once one exists) —
plain text, no icon chips, no card.
**Why it fixes flatness:** Not a layout fix so much as a hierarchy one —
right now the hero's subtitle carries all the credibility-building weight
in one dense paragraph; a second, shorter, more concrete line (in a
different size/weight step) adds a typographic beat without adding a new
visual pattern.
**Ruling constraint:** Must be honest/real (per the platform's own "no
demo, no narrow fragments" standard) — don't fabricate a stat to fill the
space.
**Cost:** Small, and copy-dependent on what's actually true to claim today.

### 8. Vary the "How it works" and "Capabilities" icon/mark treatment so the two list-style sections don't feel identical
**What:** The numbered-badge timeline (`How it works`) and the logo-row
capability groups (`Capabilities`) currently sit only ~1200px apart in the
scroll with the same left-aligned heading + list shape and nothing to
distinguish their rhythm from each other. Minor variation — e.g. slightly
different heading treatment, or the elevation-band idea from #2 applied
only to one of them — would help.
**Why it fixes flatness:** Smallest item on the list; mentioned mainly
because two consecutive sections with the same shape compounds Cause 1
rather than because either section is wrong on its own.
**Ruling constraint:** Same neutral-only constraint as #2.
**Cost:** Trivial, likely subsumed by #2 once that's done.

---

## Summary for the founder

**Top 3 causes, in one line each:**
1. Eight consecutive sections, ~3000px of scroll, one single flat
   background color the entire way down — the only separators are 1px
   hairlines (`landing.css:139`).
2. Zero product visuals exist anywhere in the repo's assets
   (`frontend/public/brand-assets/` is 100% third-party logos) — the page
   never shows the product it's selling.
3. Every non-text design tool (accent color, motion, icon weight) was
   correctly restrained per the founder's own rules, but nothing was added
   back in their place — so the restraint reads as an unfinished draft
   rather than a considered minimal design.

**Ranked fixes:** (1) real product screenshot under the hero, (2)
alternating neutral section elevation using tokens that already exist, (3)
fix the two broken stripe/salesforce logo files, (4) restrained
scroll-reveal motion (libraries already installed, unused), (5) a quiet
neutral gradient behind the hero only, (6) commit to or cut the Pillars
icons, (7) one concrete proof line near the hero, (8) minor rhythm
variation between the two list-shaped sections.

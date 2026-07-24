# Linear UI Spec — token, color, and surface plan for the fleet UI

Status: design spec only. No product code was changed to produce this
document. Verified live against `linear.app` in the Browser pane (screenshots
+ `getComputedStyle`/CSS-custom-property extraction — real measurements, not
recollection) and against a local static harness of our own real
`fleet-theme.css` + `theme-tokens.css`, served unmodified over HTTP and
rendered at 375px and 1280px, light and dark. See "Verification log" at the
end for exactly what was measured and how.

**Read this first if you only read one thing:** `docs/UI-CONTRACT.md` already
exists, is already "permanent, binding design law," and already says two of
the things this spec argues for — `§10`: *"One accent... used for
interactive/selected state only"* and `§2`: *"Accent color is used almost
never — primary action and active nav only."* This spec is not proposing a
new philosophy. It is auditing our own code against a rule we already wrote
down, finding ~15 places the code breaks it, and fixing the one input
(the accent's hue) that made "restrained" and "purple" both true at once.

---

## 0. The one-sentence summary

Our neutral scale and spacing scale are already close to Linear's — the gap
is almost entirely in **hue** (our accent sits closer to violet than Linear's
own indigo does) and in **where accent gets applied** (fills on badges,
toggles, and hover states that should be neutral washes or thin lines, per
our own contract). Fix the hue, fix ~15 call sites, and the UI reads like
Linear without copying a single literal Linear value.

---

## 1. Token system

### 1.1 Neutral palette — dark (current values, verified sound)

Our dark canvas is already within 2-3 hex steps of Linear's own measured
values. No structural change recommended here — listed for completeness and
to give build agents the full before/after picture in one place.

| Token (`theme-tokens.css`) | Our value | Linear's real value (measured) | Verdict |
|---|---|---|---|
| `--bg-canvas` | `#0a0a0c` | `--color-bg-primary` / `bg-level-0`: `#08090a` | Match, keep |
| `--bg-page` / `--bg-content` | `#0d0d0f` | `--color-bg-level-1` / `bg-panel`: `#0f1011` | Match, keep |
| `--bg-rail` | `#161618` | rail sits bare on canvas, no separate fill (`rgba(0,0,0,0)` all the way up) | We add a fill Linear doesn't; harmless (rail already reads as part of canvas), no change forced |
| `--bg-card` | `#1c1c1f` | `--color-bg-secondary`: `#1c1c1f` | Exact match |
| `--bg-card-hover` | `#212124` | (no direct equivalent measured; Linear's row hover is a translucent wash, not a solid step — see §3) | Keep as a solid-step fallback; fine |
| `--bg-inset` | `#101012` | `--color-bg-tint` / `bg-level-2`: `#141516` | Close, keep |
| `--border` | `rgba(255,255,255,0.08)` | `--color-border-primary` on canvas ≈ 11% white-equivalent | Ours is a touch fainter; optional nudge to `0.10` |
| `--border-strong` | `rgba(255,255,255,0.16)` | `--color-border-secondary` ≈ 18% white-equivalent | Close, keep |
| `--text-primary` | `#f4f4f5` | `--color-text-primary`: `#f7f8f8` | Match, keep |
| `--text-secondary` | `#a1a1aa` | `--color-text-secondary`: `#d0d6e0` (used for **resting, unselected** nav-item labels) | **Real gap** — see below |
| `--text-muted` | `#71717a` | `--color-text-tertiary`: `#8a8f98` / `--color-text-quaternary`: `#62666d` | Sits between Linear's two lower steps, keep |

**The one real neutral-palette finding:** Linear's sidebar nav items are
`color: #d0d6e0` (their "secondary" text) **at rest**, jumping to `#f7f8f8`
only on hover/active — a small, confident step. Ours are
`color: var(--text-secondary)` = `#a1a1aa` at rest
(`fleet-theme.css:470`), a much bigger drop from white. Result: our resting
rail items read dimmer/more recessed than Linear's, where unselected items
are already fully legible and the active state is a quieter relative bump
(mostly the background wash + font-weight, not a big color jump).

Recommendation: brighten dark-theme `--text-secondary` from `#a1a1aa` to
something in the `#c4c9d1`–`#d0d6e0` band. This one token feeds rail items,
breadcrumbs, panel-row labels, and tab labels — a single edit, wide effect.

```css
/* theme-tokens.css, html[data-theme="dark"] block */
--text-secondary: #c9ced6;   /* was #a1a1aa — brighter, closer to Linear's
                                 resting-label brightness (#d0d6e0 measured) */
```

### 1.2 Neutral palette — light

Not independently re-derived: Linear's marketing site is **hard-coded to
dark regardless of OS preference** (confirmed — `matchMedia('(prefers-color-
scheme: light)')` returned `true` on this machine while
`document.documentElement.dataset.theme` stayed `"dark"`), so there is no
live Linear light surface to measure. Our light tokens were evaluated for
internal consistency (do they mirror the same layering logic as our now-
verified dark tokens?) rather than against a live Linear reference. They do,
and read cleanly in the harness screenshots (see §Verification). No changes
recommended to `--bg-page`, `--bg-card`, `--border`, `--text-primary` etc. in
light mode.

### 1.3 The accent — the one token that matters most

```css
/* theme-tokens.css — both html[data-theme="dark"] and html[data-theme="light"] blocks */
--accent: oklch(57% 0.155 259);   /* was oklch(56.7% 0.158 286) */
```

That is the entire token-level fix. Every derived step
(`--accent-hover`, `--accent-strong`, `--accent-soft`, `--accent-muted`,
`--accent-ring`) is already computed from `--accent` via `color-mix()` in
`theme-tokens.css:91-96` (dark) and `:140-145` (light) — changing the one
base value re-derives all five automatically. Nothing else in the token
file needs to move. Full justification and math in §2.

### 1.4 Spacing scale — already correct, no change

```
4  8  12  16  20  24  28  32  40  48  56  64   (theme-tokens.css:36-47)
```

This is a clean, strict 4px grid. Linear's own real measurements are
*close* but not perfectly gridded (sidebar padding `8px 14px 14px 8px`,
issue-header padding `0 24px 0 31px` — `14` and `31` aren't 4px-grid
values). Our scale is, if anything, more disciplined than Linear's literal
pixels. **Recommendation: keep our scale exactly as-is.** Don't chase
Linear's odd numbers; the founder's "precise spacing" instinct is already
satisfied by a strict grid — the gap is elsewhere (radius, hue, where
accent lands), not in the scale itself.

Row-height reality check against real Linear measurements:

| Element | Ours (code) | Linear (measured live) | Verdict |
|---|---|---|---|
| Rail/nav item height | `28px` (`fleet-theme.css:465`) | `28px` exactly | Exact match |
| Rail item icon↔label gap | `10px` | `8px` | 2px off, optional micro-nudge |
| Property/panel row rhythm | `24px` row + `8px` gap = `32px` | same: `24px` row + `8px` gap = `32px` | Exact match |
| Label/tag chip height | n/a (chips vary) | `26px`, pill radius `9999px`, border `1px solid` | See §3 (list rows) |
| Icon button | `28px` square (`fleet-icon-btn`) | `24px` square, `4px` radius (issue-header icons) | Ours is slightly larger/rounder — both reasonable, see §1.5 |

### 1.5 Radii — real gap, and it's already a contract violation

```css
/* theme-tokens.css (base, correct) */
--radius-control: 8px;
--radius-card: 12px;

/* fleet-theme.css:32-33 (fleet-local override — THIS is the problem) */
--radius-control: 10px;   /* bumped up, comment says "Softer, Linear-grade" */
--radius-card: 14px;      /* bumped up, same comment */
```

Three independent sources agree the override is wrong, and none of them
agree with each other on anything else:

1. **`docs/UI-CONTRACT.md:35-38`** (binding law): *"Text button radius:
   6-8px... Icon button radius: 6-8px."*
2. **Real Linear, measured live**: sidebar nav item `border-radius: 6px`;
   issue-header icon buttons `border-radius: 4px`; kanban header buttons
   `border-radius: 2px`.
3. **Our own shipped CSS**: `fleet-theme.css:32` sets `--radius-control:
   10px` specifically *because* someone believed rounder was "Linear-
   grade." It measurably isn't — Linear's controls are tighter than ours,
   not softer.

Recommendation: delete the fleet-local override at `fleet-theme.css:32-33`
and let the fleet surface inherit the base `theme-tokens.css` values (`8px`
/ `12px`). That alone closes most of the gap to both the contract and the
live reference without inventing a new number.

```css
/* fleet-theme.css:29-33 — DELETE these two lines, keep the rest of the block */
.fleet-root {
  ...
-  --radius-control: 10px;
-  --radius-card: 14px;
  ...
}
```

### 1.6 Type scale — sizes are right, weight discipline has drifted

Sizes (`theme-tokens.css:21-26`) line up well with Linear's measured values
(their nav items are `13px`/weight `510`; our body/rail baseline is
`--text-sm: 13px` / `--weight-medium: 500` — a fractional-vs-round-number
difference, not a real gap).

The real gap is weight, and it's a second contract violation worth
flagging alongside the radius one:

**`docs/UI-CONTRACT.md:22-23`** (binding): *"Weights are 400 or 500 ONLY.
No 600/700 in UI chrome — bold is a color or size problem in disguise."*
(Marketing/auth is the one stated carve-out.)

`fleet-theme.css` uses `font-weight: 600` (or `var(--weight-semibold)`,
which resolves to `600`) at **17 call sites** in product chrome, plus
`font-weight: 550` at **5** more:

```
600:  lines 634, 1291, 1495, 1602, 1645, 1998, 2130, 2139, 2192,
      3036, 3081, 3636, 3691, 4088, 4224, 4636, 4649
550:  lines 1785, 2568, 2602, 3178, 4675
```

Examples: agent-avatar initials, panel-row values, the agent overview
title, unread conversation titles, empty-state titles. None of these are
marketing/auth surfaces — they're all product chrome the contract governs.

Two honest options, not a single mandate:
- **Strict compliance**: collapse every `600` and `550` to `500`.
- **Formalize a fractional step**: Linear itself doesn't use round 400/500/
  600 either — their measured nav-item weight is `510`, a deliberate
  fractional value Inter Variable supports. Our `550` usages are already in
  that spirit. If the team wants that nuance, amend `UI-CONTRACT.md §1` to
  read "400, 500, or a single fractional emphasis step (~550)" and drop
  only the six `600`s to `500`/`550`. Either path is a contract-compliant,
  Linear-calibrated outcome; picking one is a product-taste call, not a
  measurement question, so it's left open here rather than forced.

---

## 2. THE COLOR RULE — founder's hard requirement

### 2.1 Where the purple actually comes from

`theme-tokens.css:90` (and `:139`, the light-mode duplicate):

```css
--accent: oklch(56.7% 0.158 286);  /* distinct violet-indigo — not Linear's literal #5e6ad2 */
```

The comment is honest — it says "violet-indigo" — but that honesty is the
bug. Converted to sRGB, `oklch(56.7% 0.158 286)` is `#7164ce` /
`rgb(113,100,206)`. Measured precisely against real reference colors (all
figures below are OKLCH hue, the same coordinate system this token already
uses — a direct, apples-to-apples comparison, not a vibe check):

| Color | Hex | OKLCH hue | Reads as |
|---|---|---|---|
| Tailwind blue-600 | `#2563eb` | `262.9°` | blue |
| Tailwind blue-500 | `#3b82f6` | `259.8°` | blue |
| **Proposed accent** | **`#3a75d1`** | **`259°`** | **blue** |
| Linear `--color-indigo` / `--color-brand-bg` (their logomark/brand hex, `#5e6ad2`) | `#5e6ad2` | `275.2°` | indigo |
| Linear `--color-accent` (their live site's actual interactive accent) | `#7170ff` | `278.8°` | indigo |
| Tailwind indigo-500 | `#6366f1` | `277.1°` | indigo |
| **Our current accent** | **`#7164ce`** | **`285.8°`** | **already past Linear's own indigo, into violet** |
| Tailwind violet-500 | `#8b5cf6` | `292.7°` | violet ("purple" to most people) |
| Tailwind purple-500 | `#a855f7` | `303.9°` | purple |

Our current accent's hue (`285.8°`) sits **past Linear's own indigo
(`275°`)**, closer to Tailwind's violet-500 (`292.7°`) than to Linear's
indigo or to true blue. That's not a subjective read — it's ~11° further
into violet territory than the color this codebase's own comment claims to
be distinct from. This is why "restrained indigo" in the code and "purple
shit" in the founder's eyes are the same pixels.

### 2.2 The fix

```css
--accent: oklch(57% 0.155 259);   /* ≈ #3a75d1, rgb(58,117,209) */
```

- **Hue 259°** — matches Tailwind blue-500's OKLCH hue (`259.8°`) almost
  exactly. Unambiguously blue to any observer; not adjacent to indigo,
  violet, or purple on the wheel.
- **Chroma 0.155** — deliberately close to Linear's own *brand* chroma
  (`0.159` on their `#5e6ad2`), i.e. restrained, not neon. (Their live-site
  interactive accent `#7170ff` is actually more saturated, `0.207` — ours
  stays calmer than even that.)
- **Contrast, checked both directions** (WCAG relative luminance):
  - vs. our dark canvas `#0d0d0f`: **4.29:1**
  - vs. white (`--accent-contrast`, used as text on filled buttons):
    **4.52:1**
  - For comparison, Linear's own accent gets 5.18:1 / 3.84:1 against the
    same two anchors — ours is better-balanced across both, not worse on
    either.
- Verified live in the harness: `getComputedStyle` on `.fleet-btn--accent`
  after the token swap returned exactly `oklch(0.57 0.155 259)`, and the
  rendered screenshot (see §Verification) shows a clean blue button, no
  violet cast, in both themes.

This is a **one-line change per theme block** (two lines total, since dark
and light currently share the identical literal). No other token moves.

### 2.3 Where the accent may appear, and where it may not

Restated from the founder's own framing, now as an enforceable rule with
real classes attached:

**May appear (all currently correct or correct once re-hued — no change
needed beyond §2.2):**
- A **solid fill** on the single primary-action button per view
  (`.fleet-btn--accent`) — Linear does this too; one loud button per screen
  is the point.
- A **thin line**: the 2px active-rail indicator (`.fleet-rail-item--active
  ::before`, `fleet-theme.css:491-499`), a focus ring
  (`outline: 2px solid var(--accent-ring)`, used at 8 call sites for
  `:focus-visible`), a thin border on an *exclusive-choice* picker's
  selected card (see 2.4).
- A **tiny dot**: 4-8px unread/live/active-subitem indicators
  (`.fleet-rail-subitem-dot`, `.fleet-inbox-row-dot`, `.fleet-work-conv-dot`,
  the connector-picker's own 9px radio-fill) — these are genuinely "tiny
  accent," exactly the founder's own third bucket.
- **Inline text links** (`.fleet-link`) — Linear's own `--color-link-
  primary` is their accent blue too; this is expected, not a violation.
- **A toggle switch's ON state** (`.fleet-toggle.is-on`) — near-universal
  UI language (iOS, most web apps); once re-hued this reads as an ordinary
  blue switch, not a purple one.
- **The chat-bubble "this is the agent's message" side** (`.fleet-work-msg
  --agent`) — iMessage-style speaker coloring, expected in any chat UI.
- **Sage's own icon tile** (`.fleet-rail-sage-icon`, `.fleet-sage-tile`) —
  Sage is the one singular "operator" entity in the product; a single,
  rare, always-on brand-colored tile for the one distinguished agent is the
  same move as Linear's own colorful logomark sitting on an otherwise
  neutral UI. Keep.

**May NOT appear (found via exhaustive grep of all 75 `var(--accent…)`
usages in `fleet-theme.css`; these are fills or hover-rings on elements
that are not the one-per-screen primary action or a genuinely tiny mark):**

| # | File:line | Selector | Today | Why it's wrong | Becomes |
|---|---|---|---|---|---|
| 1 | `fleet-theme.css:1243` | `.fleet-inbox-row.is-selected` | `background: var(--accent-soft)` | Selected-row fill — exactly the "big-ish saturated wash" the founder called out | `background: var(--rail-active)` (neutral wash, matches Linear's own measured `rgba(255,255,255,.04)` selected-row treatment, pixel for pixel) |
| 2 | `fleet-theme.css:1968-1969` | `.fleet-channel-card--active` | border + `accent-soft` fill | Fill on a card selection | Keep border (thin accent = allowed), drop fill → `var(--bg-inset)` |
| 3 | `fleet-theme.css:2245-2246` | `.fleet-connector-picker-row.is-selected` | border + `accent-soft` fill | Same pattern | Same fix: keep border, drop fill |
| 4 | `fleet-theme.css:2968-2970` | `.fleet-detail-nav-toggle.is-active` | color+bg+border all accent | The file's own **correct** tab pattern two rules over (`.fleet-detail-toptab.is-active`, line 2686) uses plain `var(--rail-active)` — this one small icon toggle is the inconsistent outlier | Match the correct pattern: `background: var(--rail-active); color: var(--text-primary); border-color: transparent;` |
| 5 | `fleet-theme.css:3275-3276` | `.fleet-toggle.is-on` (track) | `accent-soft` bg + `accent-ring` border | Minor — the *knob* staying accent is fine (see 2.3 allow-list), but the track wash is redundant on top of it | Keep knob accent, simplify track to a neutral `var(--bg-inset)` |
| 6 | `fleet-theme.css:3769-3776` | `.fleet-wizard-step-dot.is-active/.is-done` | border/bg/color accent | Linear's own step indicators and checkmarks never tint with brand color — completion is communicated by fill + glyph, not hue | Monochrome: `is-active` → `var(--text-primary)` border/text; `is-done` → filled `var(--text-primary)` with `var(--bg-panel)` glyph (a plain "done" dot, not a brand moment) |
| 7 | `fleet-theme.css:3863-3880` | `.fleet-wizard-option.is-selected` (+ disabled variant) | border + `accent-soft` fill | Same "exclusive picker" fill pattern as #2/#3 | Keep border, drop fill → `var(--bg-inset)` |
| 8 | `fleet-theme.css:3904-3905` | `.fleet-wizard-option-tag` ("coming soon") | color+border accent | Linear's own "New" feature badge (measured live, next to "Coding Sessions") is plain `color: #d0d6e0` — neutral gray, not their accent | `color: var(--text-secondary); border-color: var(--border);` |
| 9 | `fleet-theme.css:4189` | `.fleet-agent-health-dot.is-stopped` | `background: var(--accent)` | Reuses the *brand* color to mean a *status* ("stopped") — mixes two different vocabularies. Linear never tints a status icon with their brand indigo; status colors are always semantic (their measured `--color-yellow`/`--color-red`/`--color-green`) | Give "stopped" its own neutral/muted tone — not accent, not full offline-red (it's deliberate, not an error). See §3.4. |
| 10 | `fleet-theme.css:4287-4288` | `.fleet-channel-chip` | `accent-soft` bg + accent text | Multiplies fast — up to 2-3 per row × every row in the Agents list. The single most visible "purple everywhere" instance in this audit (confirmed in the harness screenshot: 5 purple chips visible in a 4-row list) | Neutral chip: `background: var(--bg-inset); color: var(--text-secondary);` |
| 11 | `fleet-theme.css:4581` | `.fleet-badge--lock` (+ 3 usages: "Owner", "Granted by you", "hardware locked") | color+border+`accent-soft` fill | Persistent badge, not a hover/selection state, and reused across every access-controlled tool row — same fill-multiplication problem as #10 | Match the file's own already-neutral `.fleet-badge--preset` treatment: `background: var(--bg-inset); border-color: var(--border); color: var(--text-secondary);` |
| 12 | `fleet-theme.css:4590` | `.fleet-badge--action:hover` | `border-color: var(--accent-ring)` | **This file already states the rule it's breaking** — see lines 1963, 2240, and 3858, each literally commented `/* accent border is reserved for selected */`. A hover state getting an accent ring is the exact inconsistency those comments were guarding against | `border-color: var(--border-strong)` |
| 13 | `fleet-theme.css:4640` | `.fleet-work-list-live` | `color: var(--accent)` | Means "conversation is live right now" — that's our existing green "working" semantic wearing a second, redundant color language | `color: var(--online-text)` |
| 14 | `fleet-theme.css:4705` | `.fleet-wizard-option-note` | `color: var(--accent)` | Explanatory text ("why this option is locked"), not a call-to-action — its sibling `.fleet-wizard-option-note--gateway` already correctly uses a semantic tone instead | `color: var(--text-secondary)` |
| 15 | `fleet-theme.css:4843-4845` | `.fleet-badge--operator` ("Operator" tag next to "Sage") | color+border+`accent-soft` fill | Permanently visible the entire time you're in Sage's view, and redundant — Sage's icon tile (an allowed accent use, §2.3) already signals "this one is special"; saying it twice in accent is the founder's "purple everywhere" complaint in miniature | `background: var(--bg-inset); border-color: var(--border); color: var(--text-secondary);` |
| 16 | `fleet-theme.css:4893` | `.fleet-sage-chat-suggestion:hover` | `border-color: var(--accent-ring)` | Same hover-ring inconsistency as #12 | `border-color: var(--border-strong)` |

A ready-to-apply CSS block encoding every CHANGE row above (verified
working in the harness — see §Verification) is included at the end of this
document (§5.1) so a build agent can paste it directly rather than
re-deriving each selector.

**Not on this list, and deliberately not flagged:** `fleet-
presentation.ts:58-73`'s `TintKey = "blue" | "purple" | "amber" | …` (8
per-project identity colors). This is a user-chosen tag color, one of
eight, the same category as Linear's own per-project color picker — not a
system interaction color. The file already self-documents this
distinction ("*These are identity colors, NOT the brand accent*",
`fleet-presentation.ts:60-63`). Its `purple` swatch (`#AFA9EC` fg) is a
light lavender, visually distinct enough from the new blue accent
(`#3a75d1`) that there's no confusion risk between "this project is
tagged purple" and "this button is active." No change recommended.

### 2.4 The nuance behind "border kept, fill dropped"

Several rows above (2, 3, 7) keep a thin accent **border** while dropping
the accent **fill**. This isn't inconsistent with "purple must be rare" —
it's the founder's own stated allowance in practice: *"a faint neutral
background, a thin line, maybe a tiny accent — not a big saturated fill."*
These three selectors are all **exclusive-choice pickers** (pick exactly
one connector, one channel, one wizard option from a small set) — a thin
accent outline on the chosen card answers "which one did I pick" with the
same clarity a fill would, at a fraction of the visual weight. Linear
reserves a near-identical treatment for its own text-selection highlight
(`--color-selection-bg`, their one other resting use of brand indigo
outside interactive controls). Neutral background + thin accent line is
the "clear but not loud" middle path the rule asks for.

---

## 3. Per-surface changes

### 3.1 Rail

**Structure** (already close to Linear's, validated live): workspace
identity row → search + new-agent affordances → nav items → collapsible
Projects/Agents sub-lists → controls (theme/collapse) → pulse line →
account row. This is Linear's own sidebar shape (`workspace switcher →
search/new → nav → collapsible Teams/Projects → …`), already adopted
correctly (`PrimaryRail.tsx`).

**What changes:**
- `--text-secondary` brightened per §1.1 — resting nav-item labels
  (`.fleet-rail-item`, `fleet-theme.css:470`) get closer to Linear's fully-
  legible resting state instead of reading recessed.
- `--radius-control` drops from `10px` to `8px` per §1.5 — nav items,
  search button, new-agent button all get a half-step tighter.
- The active-item indicator (`.fleet-rail-item--active::before`, a 2px
  accent bar) and the active-subitem dot (`.fleet-rail-subitem--active
  .fleet-rail-subitem-dot`) both stay — they're the allowed "thin line" /
  "tiny dot" accent uses, and they'll simply render in the new blue.
- `.fleet-rail-item--active` and `.fleet-rail-subitem--active` backgrounds
  (`var(--rail-active)`) are **already correct** — a neutral wash, no fill
  change needed. This was already right before this audit.

**Before → after, concretely:** today, "Agents" (active) reads as: dim
gray label, thin *violet* bar on the left edge, gray wash background.
After: brighter gray label (closer to Linear's resting brightness), thin
*blue* bar, same gray wash, tighter corner radii on every button above it.

### 3.2 Breadcrumbs / header

Already structurally correct and needs no change: segment chain with a
muted `ChevronRight` separator, current segment bold, primary action
portaled onto the same row via `HeaderAction`/`HeaderActionSlotProvider`
(`Breadcrumbs.tsx:114-146`) — this is exactly Linear's own "title + count
+ primary action, one row, no second header block" pattern, independently
arrived at and already matching what I measured live (Linear's issue
header: title + star + "..." + pagination, all one 44px bar).

One small, low-cost alignment: the breadcrumb-count text
(`.fleet-breadcrumb-count`, `fleet-theme.css:227-231`, e.g. "Agents · 4")
already matches Linear's own "title + inline count" convention exactly
(confirmed live: their column headers read "Backlog · 8", "Todo · 71" in
the same inline-muted style). No change — flagged only to confirm it was
checked and is correct.

### 3.3 List rows + group headers

**Row structure is sound** — grid columns, hairline `border-bottom`
dividers (never cards), muted header row, hover wash. This already matches
Linear's own list philosophy (*"Hairline lists, not cards,"*
`UI-CONTRACT.md:185-186`).

**What changes:**
1. Channel chips (`.fleet-channel-chip`, `fleet-theme.css:4280-4293`) go
   from accent-tinted to neutral (§2.3, row 10) — this is the single
   highest-multiplication purple source in the whole audit; a 6-row list
   with 2 channels each puts 10+ purple chips on screen at once today.
2. Preset badges (`.fleet-badge--preset`, e.g. "support", "researcher")
   are **already neutral** (`bg-inset` + transparent border,
   `fleet-theme.css:4582`) — no change, included here only so the before/
   after contrast between the two badge types on the same row is explicit
   in the harness screenshots.
3. `--radius-control` tightening (§1.5) affects the avatar tile corners
   (`.fleet-agent-avatar`) and the stop/resume icon button.
4. `.fleet-agent-health-dot.is-stopped` (§2.3, row 9) moves off brand
   accent onto its own tone — recommend a desaturated version of
   `--offline-dot` (same hue family as "not running," but visually
   distinct from "broken/offline," e.g. `color-mix(in oklch, var(--offline
   -dot) 55%, var(--text-muted) 45%)`) rather than reusing either brand
   accent or full alarm-red for an owner-deliberate state.
5. Group headers (`.fleet-agent-group-header`, `fleet-theme.css:4329-
   4343`) are **already correct** — muted text, project icon, hairline
   bottom border, no change.

**Row-height note for `docs/UI-CONTRACT.md` §3:** our desktop
`.fleet-agent-row` is `min-height: 52px` and packs two lines (name +
activity preview) even at desktop width — it doesn't fit either of the
contract's existing buckets (*"desktop, one-line: 40-44px"* or *"mobile,
two-line: 44-52px"*). This isn't a violation so much as a bucket the
contract doesn't yet have a name for. Recommend adding a *"desktop,
two-line: 48-52px"* row to `§3`'s table rather than squeezing the preview
line out to fit the one-line bucket — the activity preview is real,
useful information Linear's own flatter issue rows don't carry.

### 3.4 Agent detail (tabs, properties panel, buttons, inputs)

**Tabs (`.fleet-detail-toptab`, `fleet-theme.css:2668-2686`) — already
correct, zero changes.** Active tab is `background: var(--rail-active);
color: var(--text-primary); font-weight: 500;` — no accent anywhere. This
is the pattern every other selected/active state in the file should have
matched (see §2.3 row 4, where one small icon-toggle didn't).

**Properties panel (`.fleet-detail-properties`, `PanelRow`/`PanelSection`
in `FleetRightPanel.tsx`) — already correct.** Label-left/value-right
rows, muted section titles, semantic tone colors only on values that need
one (`--online-text`/`--offline-text`/`--text-muted`). This is Linear's
own properties-panel pattern (their "In Progress / High / jori / Linear"
column, measured live: `24px` rows, `8px` gaps, no accent anywhere in the
panel either — confirmed by the full-frame hue scan in §Verification,
which found exactly two non-neutral colors in Linear's entire rendered
mockup, a green "+4" and a red "-4"). One dead/unused hook worth pruning:
`.fleet-panel-row-value--accent` (`fleet-theme.css:1503`) has no live call
site (`grep` across `FleetAgentDetail.tsx` and `tabs/*.tsx` found zero
`tone="accent"` usages) — either remove it or reserve it explicitly for a
genuinely link-like value, not leave it as an unused invitation to add
accent to a panel that's currently accent-free.

**Buttons (`.fleet-btn`, `fleet-theme.css:1796-1841`):**
- Default/secondary: `background: var(--bg-card); border: 1px solid var(
  --border); color: var(--text-primary);` — already correct, matches
  `UI-CONTRACT.md §2`'s "one border, one hover, one pressed state."
- Hover: `background: var(--bg-card-hover); border-color: var(--border-
  strong);` — correct, neutral.
- Active/pressed: `transform: scale(0.98)` — correct, matches the
  contract's "barely-there pressed state."
- Disabled: `opacity: 0.6` — correct.
- Primary (`.fleet-btn--accent`): solid fill, the one allowed full-accent
  button per view — correct pattern, just re-hues automatically (§2.2).
- **Height**: `30px` (`fleet-theme.css:1803`) vs. `UI-CONTRACT.md §2`'s
  `28px` desktop spec. A 2px nudge down closes this exactly.

**Inputs (`.fleet-wizard-input`, `.fleet-persona-textarea`,
`.fleet-overview-title-input`):** all already `border: 1px solid var(
--border); background: var(--bg-inset);`, with `:focus-visible` getting
`outline: 2px solid var(--accent-ring)` — the correct, standard "focus
rings are the one place accent belongs on an input" pattern, already
matching both Linear and the founder's own "maybe a tiny accent" carve-
out. No change.

**Toggle switches:** see §2.3 row 5 — keep the accent knob, neutralize the
track wash.

### 3.5 Empty / loading states

**Already correct, no changes.** `FleetListSkeleton` renders shaped bars
in `--rail-active` (a neutral tint), never a spinner
(`fleet-states.tsx:11-22`); `.fleet-empty`/`.fleet-page-state` pair an
icon tile in `--bg-surface-2` with an honest title + description + a real
next action (`fleet-theme.css:2542-2611`) — this matches `UI-CONTRACT.md
§10`'s "Honest empties" law and Linear's own restrained, text-forward
empty-state style. Verified no accent color anywhere in either pattern.

---

## 4. Gap analysis

Linear ships patterns we don't have, and we ship patterns Linear doesn't
need. Judged against what we actually are — an AI-agent-fleet operator
console, not an issue tracker — not against "does Linear have it."

### Adopt

**1. Agent-native "worked for Xs, here's what it did" disclosure — highest
value item in this section.** Linear's own live mockup (the "Opus 4.8"
panel I captured mid-session) shows exactly this: a collapsible "Worked
for 7s ▾" row that expands into step-by-step actions and a file diff
(`+4 -4`, in their semantic green/red, never their accent). Linear is
retrofitting this *onto* an issue tracker because agents are now a
secondary citizen of their product. **We're the opposite — agents are the
entire product.** This pattern belongs in our `AgentChat`/`WorkTab`
transcript rendering more than it belongs in Linear's. We already have the
exact disclosure primitive needed (`.fleet-disclosure`/`-trigger`/
`-chevron`/`-body`, `fleet-theme.css:3304-3327`) sitting unused for this —
recommend wiring agent transcript messages that involved multiple tool
calls/steps through it, collapsed by default, so a long agentic run
doesn't dump its entire scratchpad into the chat by default.

**2. Diff/file-change stats for coding agents.** Small addition, same
Linear reference: `+N`/`-N` line counts in existing `--online-text`/
`--offline-text` (never accent — confirmed, Linear doesn't tint these with
brand color either). Relevant the moment a Work-tab transcript involves a
code change.

**3. "Agents needing attention" count on the rail nav item.** Not
literally a Linear pattern — an extension of a pattern **we already
built**. `Inbox` already gets a live unread-count badge
(`.fleet-rail-item-count`, computed at `PrimaryRail.tsx:132-140`, rendered
at `:309-310`). `Agents`
gets nothing, even though "is anything broken right now" is arguably the
first thing a fleet owner wants on opening the app. Recommend a small
muted count (offline + error, not "stopped" — stopped is deliberate, not
a problem) on the `Agents` rail item, same visual treatment as `Inbox`'s.
This is the single best "Linear in our way" opportunity in this whole
audit: Linear can't have this (issues don't autonomously go offline),
we're built for exactly this signal, and it costs one more prop pass
through code that already exists.

**Considered, low priority — not urgent:** Linear's `02 / 145` prev/next-
through-a-filtered-list chevrons on its issue detail. Would let an owner
triage through Inbox items or page through agents without returning to
the list each time. Real but modest value; not recommended for this pass.

### Do not adopt

**4. Kanban/board view.** Linear's board exists because issues are
discrete units of work that move through a small number of human-decided
stages, and dragging a card *is* the state-change action. An agent isn't a
ticket — it's a persistent, continuously-running entity whose state
changes because it actually did something, not because an owner dragged
it. There's no drag-and-drop verb that means anything here. The value a
board provides — "what's the distribution of state across everything
right now" — is already served by the rail pulse (`2 working · 1 stopped ·
$0.04 today`) and the Now strip, sized correctly for our object (a fleet
of a few to a few dozen agents), not force-fit into Linear's object
(hundreds of tickets needing spatial triage).

**5. Cycles (time-boxed sprints).** No analog — agents don't work in
2-week sprints, they run continuously or on their own schedules. Skip
entirely.

**6. Real-time multiplayer presence (cursors, "so-and-so is viewing
this").** Linear is a multi-human collaborative editor; we're a
single-owner console for supervising software. There's no second human
whose cursor needs to be shown. Skip.

### Drop (things we do today that don't belong)

**7.** Every accent-as-fill instance in §2.3's CHANGE table — the core
deliverable of this spec.

**8.** `font-weight: 600`/`550` in product chrome (§1.6) — a real,
independent drift from our own contract, not something Linear does
either (their measured weight tops out at a fractional `510`).

**9.** `--radius-control`/`--radius-card` fleet-local overrides (§1.5) —
same story: drifted from both our own contract and the live reference in
the same rounder direction.

**10.** Reusing brand accent to mean a *status* (`is-stopped` dot, §2.3
row 9) — mixes two vocabularies (brand/interactive vs. semantic/status)
that should stay separate, the way our own `--online-dot`/`--offline-dot`/
`--degraded-dot` already correctly stay separate from `--accent`
everywhere else.

### Already correct — validated, not touched

Command palette (⌘K), `g`-then-`key` rail chords, `j`/`k` roving list
focus, overlay-not-shove drawers, hairline (not card) list rows, skeleton
(not spinner) loading, honest empty states, semantic status dots always
small (verified via grep — every `--online-dot`/`--offline-dot`/
`--warning-text`/`--degraded-dot` usage in `fleet-theme.css` is on an
element ≤8px, never a background fill), and the properties-drawer-for-
lists / permanent-properties-column-for-detail split. All of these are
independently-arrived-at matches to Linear's own real patterns, several
with code comments that already cite Linear as the reference. Called out
explicitly so nobody "fixes" something that was already right.

---

## 5. "Linear in our way"

Places we should deliberately diverge, because we're a fleet console, not
an issue tracker — and a couple of places we're already doing this well
and should keep doing it.

- **The rail pulse footer** (`2 working · 1 stopped · $0.0412 today`,
  `fleet-theme.css:570-585`) has no Linear equivalent — issues don't have
  a live "pulse." This is exactly right for us and should stay exactly as
  quiet as it is (the code's own comment calls it "whisper-quiet... never
  a widget competing with the nav" — correct instinct, keep it, don't
  amplify it). Where we should extend the *idea*, not the volume: the
  "Agents needing attention" badge in §4's gap analysis is the right way
  to surface more, not making the pulse line louder.
- **Unrounded, honest cost figures** (`$0.0071`, not `$0.00`) — no Linear
  analog (they don't meter anything per-row), and already a deliberate,
  well-reasoned choice in this codebase (see the `money()` comments in
  `PrimaryRail.tsx:46-51` and `AgentsList.tsx:45-48` citing the prior
  Truth Map finding that rounding to 2dp silently lied about real spend).
  Keep.
- **Status taxonomy tuned for autonomous entities** (working / ready /
  offline / stopped / error, with the pulsing dot reserved for the one
  state that's "visibly alive") instead of Linear's workflow-stage
  taxonomy (backlog / todo / in progress / done). Correctly different
  because the underlying object is different — an agent isn't a ticket
  moving through stages a human assigns, it's a thing that's either
  running, resting, or broken. Keep this divergence.
- **Owner/customer access-control toggles** (Tools tab — "Owner can
  message anytime," "Customers can message directly," per-tool grants) —
  a trust-boundary concept with no Linear equivalent, because Linear
  doesn't have a "this thing talks to your customers" problem. Already a
  sound, specific-to-us feature; the only change it needs is the neutral-
  badge fix from §2.3 (rows 11, 15), not a redesign.
- **Where we should go further than Linear, not just match it**: Linear's
  own accent-rarity is aspirational-but-imperfect even on their real
  product (their toggle/selection states aren't all documented publicly,
  and their own historic brand hex `#5e6ad2` sits closer to indigo than
  the "blue" this spec recommends for us). Landing at a hue that's more
  unambiguously blue than Linear's own puts us in a stronger position to
  defend "never purple" going forward — there's no color-wheel judgment
  call left to make on any future component; blue is blue.

---

## 5.1 Drop-in CSS (the whole fix, one block)

Every change from §1.3, §1.5, and §2.3's CHANGE table, in our real token
names, ready to paste into `theme-tokens.css`/`fleet-theme.css`. This is
the literal content that was loaded into the harness for the "after"
screenshots in the verification log below.

```css
/* ── theme-tokens.css ── */
/* In BOTH html[data-theme="dark"] and html[data-theme="light"] blocks: */
--accent: oklch(57% 0.155 259);            /* was oklch(56.7% 0.158 286) */
/* dark block only, brightness fix from §1.1: */
--text-secondary: #c9ced6;                  /* was #a1a1aa */

/* ── fleet-theme.css ── */
.fleet-root {
  /* DELETE these two lines entirely — inherit the base 8px/12px (§1.5) */
  /* --radius-control: 10px; */
  /* --radius-card: 14px; */
}

.fleet-btn { height: 28px; }                /* was 30px — UI-CONTRACT §2 */

/* Selected/active → neutral wash (was accent-soft fill) */
.fleet-inbox-row.is-selected { background: var(--rail-active); }
.fleet-detail-nav-toggle.is-active {
  background: var(--rail-active);
  color: var(--text-primary);
  border-color: transparent;
}

/* Persistent badges → neutral (matches fleet-badge--preset already) */
.fleet-badge--lock {
  color: var(--text-secondary);
  border-color: var(--border);
  background: var(--bg-inset);
}
.fleet-badge--action:hover:not(:disabled) { border-color: var(--border-strong); }
.fleet-badge--operator {
  color: var(--text-secondary);
  border-color: var(--border);
  background: var(--bg-inset);
}
.fleet-wizard-option-tag { color: var(--text-secondary); border-color: var(--border); }
.fleet-wizard-option-note { color: var(--text-secondary); }

/* Hover rings → neutral (the file already says "accent border is reserved
   for selected" at lines 1963/2240/3858 — these were the inconsistency) */
.fleet-sage-chat-suggestion:hover { border-color: var(--border-strong); }

/* Exclusive-choice pickers: neutral fill, thin accent BORDER kept */
.fleet-channel-card--active,
.fleet-connector-card--active {
  background: var(--bg-inset);
  border-color: var(--accent);
}
.fleet-connector-picker-row.is-selected { background: var(--bg-inset); border-color: var(--accent); }
.fleet-wizard-option.is-selected,
.fleet-wizard-option:disabled.is-selected:hover {
  background: var(--bg-inset);
  border-color: var(--accent);
}

/* Wizard step indicator → monochrome */
.fleet-wizard-step-dot.is-active .fleet-wizard-step-index {
  border-color: var(--text-primary);
  color: var(--text-primary);
}
.fleet-wizard-step-dot.is-done .fleet-wizard-step-index {
  background: var(--text-primary);
  border-color: var(--text-primary);
  color: var(--bg-panel);
}

/* Channel chip → neutral (the single biggest purple-multiplier in the app) */
.fleet-channel-chip { background: var(--bg-inset); color: var(--text-secondary); }

/* "Live" indicator: reuse working/online green, not a second accent language */
.fleet-work-list-live { color: var(--online-text); }

/* "Stopped" status dot: off brand accent, its own muted tone */
.fleet-agent-health-dot.is-stopped {
  background: color-mix(in oklch, var(--offline-dot) 55%, var(--text-muted) 45%);
}
```

**Companion edit, not in this file:** `docs/UI-CONTRACT.md:187-189` cites
the exact old oklch value by name (*"the violet-indigo `oklch(56.7% 0.158
286)`"*). That line must be updated in the same change as the token edit —
otherwise the contract and the code will disagree about what "the one
accent" is, the day this ships.

---

## Verification log

Browser pane was available and used for all of this — no fallback to
memory/training-data recall of Linear was needed.

**Linear (live, `linear.app`):**
- Navigated to `linear.app` (home) and `linear.app/features`; the home
  page's hero renders a large, fully-interactive, real-CSS-module coded
  mockup of the actual app (sidebar, an open issue with a live "Opus 4.8"
  agent panel, a Backlog/Todo/In Progress/Done board, an agent chat
  composer) — not a static image. `/features` is scroll-animation-gated
  (content mounts on real scroll position, didn't yield additional DOM on
  programmatic `scrollTo`) and added no further usable data beyond the
  homepage in the time spent there.
- Took full-page and viewport screenshots (dark; the site ignores
  `prefers-color-scheme` and stays dark regardless of OS setting — checked
  directly via `matchMedia` + `document.documentElement.dataset.theme`).
- Extracted real values via `getComputedStyle` and by reading
  `getComputedStyle(document.documentElement)`'s full `--color-*` custom-
  property set — this pulled Linear's actual design-token sheet directly
  (`--color-accent: #7170ff`, `--color-indigo: #5e6ad2`, the full
  `--color-bg-level-0..3` / `--color-border-*` / `--color-text-*` ramps,
  etc.), not estimates.
- Measured concrete boxes: sidebar (`232px` wide, `8px 14px 14px 8px`
  padding), nav items (`28px` tall, `6px` radius, `13px`/`510` weight),
  the "selected" nav item (`background: rgba(255,255,255,.04)`, no other
  change), properties panel rows (`24px` + `8px` gap), issue-header icon
  buttons (`24px`, `4px` radius), a label chip (`26px`, pill radius, `1px`
  border), a Button-system's `ghost`/`secondary`/`mini`/`small` ARIA
  variants, and a full-frame scan for every non-neutral (hued) computed
  color in the rendered mockup — found exactly 2 (a green `+4`, a red
  `-4`), everything else neutral or the two greyscale text ramps.
- Attempted a live `:hover` capture via synthetic mouse move; the browser
  tool's hover didn't persist into the following JS evaluation (a known
  limitation, not a data gap — the "selected" state's static DOM class
  gave the more important data point directly, and hover is inferable
  from the existing wash tokens with high confidence).

**Our own UI (static harness, real files, unmodified):**
- Two local HTTP servers: one serving `frontend/` read-only (so
  `theme-tokens.css`/`fleet-theme.css` loaded were the exact, byte-for-
  byte real files — never copied or edited), one serving two hand-built
  harness pages (`list.html`: rail + breadcrumbs + agents list with a
  group header, mirroring `AgentsList.tsx`'s real DOM shape exactly;
  `detail.html`: rail + breadcrumbs + tab strip + properties panel +
  buttons + toggle + badge + input, mirroring `FleetAgentDetail.tsx`).
- Rendered and screenshotted at **1280px** (desktop) and **375px**
  (mobile) — both **light and dark** — confirming the mobile two-line row
  collapse (`.fleet-agent-row-mobile`) and the tab-strip horizontal-scroll
  fade both work as coded.
- Screenshotted every configuration **before** (real, unmodified tokens)
  and **after** (real tokens + the §5.1 override injected as a `<style>`
  tag at runtime — never written into the real files) — 9 screenshots
  total. The purple-to-blue change is visually unambiguous in every pair:
  the "New agent" button, channel chips, "Owner only" badge, and toggle
  knobs all shift from violet to a clean blue with no other visual change,
  in both themes.
- Confirmed via `getComputedStyle` after injection that
  `.fleet-btn--accent`'s background resolved to exactly
  `oklch(0.57 0.155 259)` — the token pipeline (base value → `color-mix()`
  derived steps) works correctly with the new value, no knock-on breakage.
- Harness files and both local servers were scratch-only
  (`/private/tmp/.../scratchpad/harness/`, ports 8911/8912 on
  `127.0.0.1`) — nothing was written into the repository to produce this
  document. Both servers were stopped and the harness directory removed
  at the end of this session; nothing persists outside this spec file.

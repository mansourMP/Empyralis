# UI Contract

Permanent, binding design law for every future UI change in this codebase —
desktop and mobile, every surface. Numbers, not adjectives. When a PR's
diff disagrees with a number in this file, the file wins; change the file
first, in its own review, then the PR.

Calibration reference: Linear's own web + mobile density. Not "inspired
by" — match the numbers.

> **Terminology note (2026-07-23):** "Sage" is legacy product terminology — the
> platform has only agents (owner-facing, customer-facing serving the owner,
> and AskAI). The "Ask Sage launcher" references below were written before
> the label rename shipped; `SageLauncher.tsx` renders "Ask AI" today and
> says so in its own header, while the component/route/class names still
> say Sage. Read them as naming that component, not as this document
> endorsing "Sage" as live product naming.

## 1. Type

| Context | Size | Weight |
|---|---|---|
| Desktop UI body text | 13px | 400 |
| Mobile list-primary (row title, message text) | 15px | 400/500 |
| Meta / secondary (timestamps, counts, muted lines) | 12px | 400 |
| Chat bubble text | 15px | 400 |

Rules:
- Weights are 400 or 500 ONLY. No 600/700 in UI chrome — bold is a color
  or size problem in disguise. (Headings in marketing/auth surfaces are the
  one carve-out; this contract governs product chrome.)
- Sentence case everywhere. Never Title Case, never ALL CAPS (the one
  exception: single-letter/short badges like chip abbreviations, e.g. "TG").
- Numbers that represent quantities (costs, token counts, list counts) are
  `font-variant-numeric: tabular-nums` so columns of digits align.

## 2. Controls

| Control | Desktop | Mobile |
|---|---|---|
| Text button height | 28px | 32-34px |
| Text button padding | 0 12px | 0 12px |
| Text button radius | 6-8px | 6-8px |
| Text button font | 13px / 500 | 13px / 500 |
| Icon button | 28px square | 34px square |
| Icon button radius | 6-8px | 6-8px |

A primary action is a small labeled button — `+ New agent`, `Save`,
`Create key`. NEVER a giant full-width color block. The one exception:
a bottom-sheet's own pinned CTA row (wizard Next/Finish, a confirm sheet's
primary action) — full-width is correct THERE because the sheet's bottom
edge is the entire control's frame, not because it's mobile.

Icon buttons are square with 6-8px corner radius, not circles — a circle
around a single glyph reads as a toy affordance at this density.

### Button surface — no exceptions

A button is a single flat surface: hairline border, one subtle hover
state, one barely-there pressed state (scale 0.98). NO nested frames, NO
stacked layers, NO concentric accent rings, NO multi-stage animation.
Accent color is used almost never — primary action and active nav only.
A more decorated button is always the wrong button.

**Compliant** — one border, one hover, one pressed state, done:
```css
.btn {
  border: 1px solid var(--border);
  background: var(--bg-card);
  border-radius: 8px;
  transition: background 120ms ease-out, transform 120ms ease-out;
}
.btn:hover { background: var(--bg-card-hover); }
.btn:active { transform: scale(0.98); }
```

**Violating** — five separate decorations for one button (gradient fill,
ring shadow, an inner pseudo-element layer, hover-scale-up plus a glow,
and an active-state rotation). Every one of these reads as "the important
button" even when it's a Cancel action:
```css
.btn {
  border: 2px solid var(--accent);
  background: linear-gradient(180deg, var(--accent-soft), transparent);
  box-shadow: 0 0 0 3px var(--accent-ring), inset 0 1px 0 rgba(255,255,255,.3);
  border-radius: 10px;
}
.btn::before { /* inner glow layer */ }
.btn:hover { transform: scale(1.03); box-shadow: 0 4px 16px var(--accent-ring); }
.btn:active { transform: scale(0.95) rotate(-1deg); }
```

## 3. Rows

| Row type | Height | Notes |
|---|---|---|
| List row, mobile, two-line | 44-52px | status dot + name / muted meta line |
| List row, desktop, one-line | 40-44px | column grid, see toolbar/list docs |
| Nav row (rail, drawer) | 40px | icon + label |
| Sub-nav row (rail subnav, drawer subitem) | 36px | indented, no icon or small icon |
| Status chip (non-interactive: Ready, Online, Degraded, …) | ~17-20px | 12px text minimum, dot + label, never clickable |
| Pill / tag (interactive: filters, selectable tags) | 22-24px | 12px text, never taller |

A status chip is a compact, non-interactive readout (Linear-lean) —
deliberately smaller than an interactive pill, not an unmeasured accident.
`.fleet-schip` (the shared StatusChip component) sits in the status-chip
band. Nudge a status chip's height toward 20px only if it reads cramped on a
real phone; never grow it to the interactive band's 22-24px — that would
make a readout look clickable.

## 4. Topbar (mobile)

≤48px tall, full stop. Contents, left to right:

`[hamburger 34px] [title + count, inline] [compact primary action]`

- Title: 15px/500. Count: 12px muted, same line (`Agents · 4`, not a
  second row).
- The primary action (when a page has one) is a compact button per §2 —
  32px tall, sized to its label, not a full-width block eating the bar.
- Nothing in the topbar is taller than the bar itself. If a control needs
  more headroom than 48px gives it, it doesn't belong in the topbar —
  move it into the page body or a sheet.

## 5. Drawer (mobile nav)

- Width: max 320px, capped at ~85vw on very narrow devices.
- Structure top to bottom: workspace header row, 36px search field, nav
  rows (40px) with sub-rows (36px) indented beneath their parent, footer
  pulse line, account row.
- Slides in 200ms, scrim behind it. No floating element (Ask Sage bubble,
  help button) renders above the drawer or its scrim while it's open —
  see §7.

## 6. Toolbar order — INVARIANT

Every page with a list toolbar (Agents, Projects, project detail, and any
future list surface) renders controls in this exact order, left to right,
on EVERY viewport width:

```
[usage/stat display]  [filter + sort]  [panel-toggle]
```

`panel-toggle` is ALWAYS the rightmost control — it sits physically
adjacent to the panel edge it opens/closes. This is a hard invariant, not
a default: the specific bug this codifies was the panel-toggle button
migrating to a different position at a narrower breakpoint, orphaning it
from the panel it controlled. A future change that reorders this toolbar
at ANY width is a contract violation, not a redesign — it needs a contract
edit first.

Icon buttons in this toolbar are 34px mobile / 28px desktop per §2, square,
never circular.

## 7. Floating elements

Ask Sage launcher, the help bubble, and any future floating action button:

- Never render above an open drawer, sheet, or its scrim. Either hidden
  outright while one is open, or stacked beneath the scrim in z-index —
  a floating bubble bleeding over an open sheet is a layering bug, not a
  visual accident to tolerate.
- Fixed position, bottom-right, both themes, every viewport.

## 8. Popovers, sheets, and dialogs

- Filter/display/panel popovers on mobile are anchored sheets or popovers
  sized per this contract's own scale (§2, §3 row heights inside them) —
  never a desktop popover simply shrunk to fit a narrow viewport.
- Wizards and dialogs on mobile are full-screen sheets: compact header
  (title + close, not a desktop-density header block), body scrolls, and
  the CTA row (Cancel/Back + Next/Finish/Save) is pinned to the sheet's
  true bottom edge — full width, not a small button pair floating mid-card
  with dead space above it. If a dialog is short enough that a full-screen
  treatment would leave the CTA row stranded far from its content, it is
  a **centered dialog** instead (full-width within a comfortable margin,
  rounded on all four corners, vertically centered) — never a bottom-sheet
  shell wrapped around a small-dialog-sized CTA footer. Pick one shape
  per dialog; don't mix a sheet's silhouette with a dialog's footer.

## 9. Chat

- Composer is sticky at the viewport/panel bottom, above the keyboard,
  never part of the scrolling thread.
- Bubbles: max 85% of the thread's width. Text 15px.

## 10. Standing laws (apply everywhere, every surface, both themes)

- **Overlay, never shove.** A panel, drawer, or sheet layers over content
  on open. It never compresses, reflows, or pushes the content beneath it
  to make room.
- **Hairline lists, not cards.** A list row is a `border-bottom` hairline
  in a flat list, not a bordered/shadowed card per row.
- **One accent, almost never used.** A single accent color (`--accent`, a
  calm unambiguous BLUE, `oklch(57% 0.155 259)` ≈ `#3a75d1` — never violet
  or indigo) is the only saturated color in product chrome. It's reserved
  for at most two things: the single primary action on a page, and the
  active top-level nav item (as a thin left-border, never a fill). It is
  NOT a general "interactive/selected" color — that phrasing used to be
  this rule's loophole and is exactly how purple ended up on badges, chips,
  toggles, and list rows. Concretely:
  - Hover, on anything, is a neutral wash (`--rail-active` /
    `--bg-card-hover`), never an accent tint.
  - Selected/active (a toolbar toggle, a picker option, a filter) is a
    neutral fill (`--bg-inset`) plus a font-weight bump (400→500), never
    accent text or an accent background — including inside an otherwise
    "exclusive picker" like a sort-by list.
  - Badges, chips, and pills (preset tags, channel tags, access badges)
    are neutral (`--bg-inset`/`--border`/`--text-secondary`), full stop.
  - Keyboard focus rings and roving-focus highlights (`:focus-visible`,
    the rail's `j`/`k` highlight) are exempt — they're a11y affordances,
    not resting decoration, and conventionally use the accent everywhere.
  - Semantic status (online/offline/working/stopped/error/degraded) is a
    separate vocabulary (`--online-*`/`--offline-*`/etc.) and is never
    "the accent," even though it's also saturated color in chrome.
  Every other surface is near-neutral grayscale.
- **Identity avatars are circles, not rounded-square icon chips.** A
  person/agent identity mark (an initial-letter avatar for an agent, a
  user) is a full circle (`border-radius: 999px`) — the Slack/Linear/Gmail
  "team member" convention. A rounded-square tinted tile with a glyph
  inside is reserved for non-identity icon marks (a project's icon+tint,
  an icon button) — never reuse the square-chip shape for something meant
  to read as "a who," not "a what."
- **Monochrome motion, ≤200ms.** Transitions are opacity/transform only,
  ease-out, capped at 200ms (the shared tokens run 100-180ms). No spring,
  no bounce, no overshoot.
- **Honest empties.** An empty state names what's missing and what to do
  about it in real words ("No agents yet — create your first one to get
  started"), never a bare "No data" or a spinner that never resolves.
- **en-US dates.** `Intl`/`toLocaleDateString` calls are pinned to the
  `en-US` locale — this is a single-locale product today, not
  locale-negotiated from the browser.
- **Both themes.** Every rule in this file, every component, every state
  (including empty/error/loading) is verified in light AND dark before
  it ships.
- **The concept-renders-identically rule.** A concept that exists on
  multiple surfaces — a status chip, a cost figure, an empty state, a
  toolbar — renders with the same shape, size, and behavior everywhere it
  appears. This applies ACROSS VIEWPORTS as much as across pages: a status
  chip is not one shape on desktop and a different one on mobile just
  because there was more room to improvise.

---

Any UI build must verify against this file by DOM measurement and attach
the table. Unmeasured = not done.

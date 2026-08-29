/**
 * Design tokens, ported from frontend/lib/ui/theme-tokens.css.
 *
 * THE PAGE IS PURE BLACK, and that is a deliberate, already-settled
 * divergence from the web — CLAUDE.md records the founder's own call ("I
 * want pure black in my phone application, not like what I have on the
 * platform") and the arithmetic the iOS app used to re-derive the rest of
 * the dark ramp against #000 rather than patching one value. Those derived
 * values are REUSED here verbatim rather than re-derived, so the two mobile
 * clients cannot disagree about what "card" means while both call it that.
 *
 * Why each surface is where it is (from that derivation):
 *   - bgInset INVERTS DIRECTION on black. On the web "inset" is carved
 *     BELOW the page; there is nothing below #000, so it becomes a faint
 *     raise. Same role, opposite direction.
 *   - the hairline ALPHA is raised (0.08 -> 0.14). An alpha line loses ~10%
 *     of its contrast composited over black instead of over #1A1A1A — the
 *     same token, visibly fainter. Reusing the web's number is how borders
 *     vanish on OLED.
 *
 * ACCENT: violet, and it appears on exactly one thing per view — the single
 * primary action. Never on selected states, tabs, badges, dots or rings.
 * That is machine-enforced on the web (lib/ui/accent-restraint.test.ts);
 * here it is enforced by there being nowhere else to reach for it.
 *
 * UNREAD IS NOT THE ACCENT. The unread marker is a distinct status hue,
 * sitting beside `online` green and `warning` amber in exactly the same
 * role, so marking a row unread never spends the view's one accent. Using
 * violet for it would be the "everything important becomes purple" drift
 * that took five passes to undo on the web.
 */

export const Theme = {
  // Surfaces — the pure-black ramp.
  bgPage: '#000000',
  bgInset: '#161616',
  bgRail: '#191919',
  bgCard: '#1C1C1C',
  bgLift: '#242424', // a pressed/selected fill that still reads as the same family
  // MEASURED off the reference, not chosen: #2E2E2E is the fill Linear uses
  // for both a list row's icon well and the pill behind a selected tab.
  // bgLift (#242424) was tried there first and is 8 levels off bgCard, which
  // on a black page is a selection nobody can see — the reference's own
  // delta is ~20. One value, because in the reference both are literally the
  // same surface: "lifted off the page enough to read as a control".
  bgRaised: '#2E2E2E',

  // Hairlines. Opaque rather than alpha where they sit on a known surface,
  // so what renders is knowable instead of a function of what is behind it.
  border: 'rgba(255,255,255,0.14)',
  borderStrong: 'rgba(255,255,255,0.24)',

  // Text — unchanged from the web's dark theme; every one of these GAINS
  // contrast on black, so none of them needed re-deriving.
  textPrimary: '#F4F4F5',
  textSecondary: '#CECECE',
  textMuted: '#9A9A9A',
  // A read row dims as a whole rather than changing its background. TWO
  // levels, because the reference keeps the title/subtitle hierarchy INSIDE
  // the dimmed state (measured: title 120, subtitle 91) — collapsing them
  // to one grey makes a read row read as a single smudge instead of a row
  // that is still legible, just no longer asking for anything.
  textDimmed: '#6E6E6E',
  textDimmedDeep: '#5B5B5B',

  // The one accent, spent on the one primary action in a view.
  accent: '#A56DDE', // oklch(64% 0.17 305), dark theme
  accentText: '#FFFFFF',

  // Status hues. Named for what they mean, never "the accent".
  unread: '#3B82F6',
  online: '#22C55E',
  warning: '#FBBF24',
  danger: '#F87171',
} as const;

export const Space = {
  x1: 4,
  x2: 8,
  x3: 12,
  x4: 16,
  x5: 20,
  x6: 24,
  x8: 32,
} as const;

/**
 * SIZES ARE MEASURED OFF THE REFERENCE, not picked. Each was read twice, by
 * two independent methods, from a 3x screenshot of the app we are being held
 * to — cap height / 0.72, and again from the rendered width of a known
 * string — and the two agreed:
 *
 *   title      cap 53px -> 24.5pt   "Inbox" 187px wide -> 24pt bold
 *   rowTitle   cap 36px -> 16.7pt
 *
 * `title` was 32 and `rowTitle` 15, so the header shouted and the rows
 * whispered — a third again too much separation between them. There is no
 * `tab` size any more: the tab bar carries icons only.
 */
export const Type = {
  // Large bold left title, the header idiom.
  title: { fontSize: 24, fontWeight: '700' as const, letterSpacing: -0.4 },
  rowTitle: { fontSize: 17, fontWeight: '600' as const, letterSpacing: -0.2 },
  rowSubtitle: { fontSize: 15, fontWeight: '400' as const },
  body: { fontSize: 15, fontWeight: '400' as const },
  button: { fontSize: 15, fontWeight: '600' as const },
} as const;

export const Radius = {
  row: 10,
  card: 14,
  control: 12,
  pill: 999,
} as const;

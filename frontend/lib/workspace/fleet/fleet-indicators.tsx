import type { CSSProperties, ReactNode } from "react";

import { type AgentStatusTone, type TintKey } from "./fleet-presentation";

/**
 * The fleet's shared visual-indicator vocabulary. One place so a status dot in
 * a list row, a chip in the hardware list, and a colored value in the right
 * panel all read as the same language — Linear's restrained "color carries
 * meaning, gray is just chrome" treatment. No drop shadows anywhere here
 * (depth is border + fill only).
 */

/** A bare colored status dot — green online, red offline, neutral unknown. */
export function StatusDot({ tone, size = 8 }: { tone: AgentStatusTone; size?: number }) {
  return (
    <span
      className={`fleet-sdot fleet-sdot--${tone}`}
      style={{ width: size, height: size }}
      aria-hidden
    />
  );
}

/** Dot + label, the whole chip tinted by tone. Used wherever a status was
 *  previously undifferentiated gray text. */
export function StatusChip({ tone, label }: { tone: AgentStatusTone; label: string }) {
  return (
    <span className={`fleet-schip fleet-schip--${tone}`}>
      <span className="fleet-schip-dot" aria-hidden />
      {label}
    </span>
  );
}

// ── Agent sigil — a deterministic abstract-shape identicon ──────────────────
// Replaces the old first-letter avatar glyph: every agent id draws its own
// small mark made of 3 overlapping geometric primitives (circle/square/
// diamond/triangle), positioned, sized, and rotated from a hash of the
// agent's id. Same id -> byte-identical mark on every render (and for every
// viewer); different ids spread out visibly because the hash feeds a PRNG,
// not a lookup table with collisions. Pure inline SVG, no network fetch, no
// external identicon library.

const SIGIL_SHAPE_KINDS = ["circle", "square", "diamond", "triangle"] as const;
type SigilShapeKind = (typeof SIGIL_SHAPE_KINDS)[number];

/** FNV-1a — a small, well-distributed non-cryptographic string hash. Only
 *  needs to be deterministic and spread out, not secure. */
function hashSeed(input: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < input.length; i++) {
    h ^= input.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** mulberry32 — a tiny deterministic PRNG. The point of seeding it from
 *  hashSeed() is that the same seed always produces the same sequence, so
 *  the shapes below never shuffle between renders or between two people
 *  looking at the same agent. */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

type SigilShape = { kind: SigilShapeKind; cx: number; cy: number; size: number; rotation: number; opacity: number };

/** 3 shapes inside a 24x24 viewBox, each independently placed/sized/rotated/
 *  faded from the seeded PRNG — enough variety to read as a distinct mark
 *  per agent without ever producing an unreadable jumble. */
function buildSigilShapes(seed: number): SigilShape[] {
  const rand = mulberry32(seed);
  const shapes: SigilShape[] = [];
  for (let i = 0; i < 3; i++) {
    shapes.push({
      kind: SIGIL_SHAPE_KINDS[Math.floor(rand() * SIGIL_SHAPE_KINDS.length)],
      cx: 5 + rand() * 14,
      cy: 5 + rand() * 14,
      size: 6 + rand() * 8,
      rotation: Math.floor(rand() * 360),
      opacity: 0.5 + rand() * 0.5,
    });
  }
  return shapes;
}

function SigilShapeGlyph({ kind, size }: { kind: SigilShapeKind; size: number }) {
  const half = size / 2;
  if (kind === "circle") return <circle r={half} fill="currentColor" />;
  if (kind === "square") return <rect x={-half} y={-half} width={size} height={size} rx={size * 0.22} fill="currentColor" />;
  if (kind === "diamond") {
    return <rect x={-half} y={-half} width={size} height={size} rx={size * 0.12} fill="currentColor" transform="rotate(45)" />;
  }
  const h = size * 0.95;
  return <polygon points={`0,${-h / 2} ${size / 2},${h / 2} ${-size / 2},${h / 2}`} fill="currentColor" />;
}

/**
 * A small generated identicon for an agent — 3 overlapping geometric shapes
 * deterministically laid out from `seed` (pass the agent's stable id).
 * Renders in `currentColor`, so it inherits whatever tint (e.g. TintTile's
 * --tile-fg) the caller already applies — no new color system, it reuses
 * the fleet's existing per-agent identity tints.
 *
 * `avatarUrl` is accepted so a future explicit per-agent avatar can override
 * the generated mark with no caller changes — nothing sets it yet, so every
 * agent draws its generated sigil by default.
 */
export function AgentSigil({
  seed,
  size = 16,
  avatarUrl,
}: {
  seed: string;
  size?: number;
  avatarUrl?: string | null;
}) {
  if (avatarUrl) {
    return <img src={avatarUrl} alt="" width={size} height={size} style={{ borderRadius: "50%", display: "block" }} />;
  }
  const shapes = buildSigilShapes(hashSeed(seed || "agent"));
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden focusable="false">
      {shapes.map((s, i) => (
        <g key={i} transform={`translate(${s.cx} ${s.cy}) rotate(${s.rotation})`} opacity={s.opacity}>
          <SigilShapeGlyph kind={s.kind} size={s.size} />
        </g>
      ))}
    </svg>
  );
}

/** A rounded leading icon/initial tile for a list row. Neutral by default
 *  (`--rail-active` fill, `--text-secondary` glyph — the fallback baked into
 *  `.fleet-tile` itself). `accent`/`danger` are semantic variants (a quiet
 *  inset, and the destructive tint for a tile standing in for a real
 *  error/failed state) — neither is decoration, so neither takes `tint`.
 *
 *  `tint` (`TintKey`) DID exist here once, got removed in a colour-
 *  discipline pass, and is back — this is not that removal quietly
 *  reverting. What got removed was a hue picked by HASHING an id: nobody
 *  ever chose it, so it told a reader nothing their eyes on the label text
 *  didn't already. What's back is the opposite kind of value — a colour a
 *  PERSON explicitly picked (fleet-project-identity.tsx's
 *  ProjectIdentityPicker is the one caller today; see that file's banner
 *  for the full "reversal, and why it isn't one" writeup). Same TintKey
 *  type, same TINTS lookup, opposite reason it's on screen — decoration a
 *  human authored is information, not noise. Every other caller of this
 *  component still gets the plain neutral tile unless it explicitly passes
 *  `tint`; nothing was widened by default.
 *
 *  The colour-as-legend use case (multiple agents' cost lines on one
 *  chart, where hue is the ONLY way to tell a line from its label) never
 *  went through this component at all — that still reads TINTS/
 *  tintKeyForIndex directly (fleet-sparkline.tsx's MultiSeriesChart,
 *  billing/page.tsx). */
export function TintTile({
  accent,
  danger,
  tint,
  size = 28,
  children,
}: {
  accent?: boolean;
  danger?: boolean;
  /** A person-chosen identity colour — never set from a hash. */
  tint?: TintKey;
  size?: number;
  children: ReactNode;
}) {
  // The tint is emitted as data-tint and resolved in CSS, never inlined as a
  // literal colour: a literal cannot follow the theme, and the one set that
  // used to be inlined here was authored for dark only.
  const style: CSSProperties = { width: size, height: size };
  const variant = accent ? " fleet-tile--accent" : danger ? " fleet-tile--danger" : "";
  return (
    <span className={`fleet-tile${variant}`} style={style} data-tint={tint}>
      {children}
    </span>
  );
}

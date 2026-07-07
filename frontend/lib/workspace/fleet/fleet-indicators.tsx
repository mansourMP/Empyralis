import type { CSSProperties, ReactNode } from "react";

import { TINTS, type AgentStatusTone, type TintKey } from "./fleet-presentation";

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

/** A rounded, tinted leading icon/initial tile — gives a list row a colored
 *  anchor instead of a bare gray glyph. `tint` picks a per-identity hue;
 *  omit for the neutral surface fill. `accent` uses the brand accent tint. */
export function TintTile({
  tint,
  accent,
  size = 28,
  children,
}: {
  tint?: TintKey;
  accent?: boolean;
  size?: number;
  children: ReactNode;
}) {
  const style: CSSProperties = { width: size, height: size };
  if (tint) {
    (style as Record<string, string>)["--tile-bg"] = TINTS[tint].bg;
    (style as Record<string, string>)["--tile-fg"] = TINTS[tint].fg;
  }
  return (
    <span className={`fleet-tile${accent ? " fleet-tile--accent" : ""}`} style={style}>
      {children}
    </span>
  );
}

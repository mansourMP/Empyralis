"use client";

import { useEffect, useRef, useState } from "react";
import {
  Anchor,
  Box,
  Briefcase,
  Check,
  Compass,
  Flag,
  Flame,
  FolderKanban,
  Gem,
  Globe,
  Layers,
  Package,
  Puzzle,
  Rocket,
  Shield,
  Star,
  Target,
  Zap,
  type LucideIcon,
} from "lucide-react";

import { TintTile } from "./fleet-indicators";
import { TINT_LABELS, TINTS, type TintKey } from "./fleet-presentation";

/**
 * Project identity — icon + colour.
 *
 * REVERSAL OF RECORD, 2026-09-01. The comment that used to live here said
 * per-project colour was dropped on purpose: "There is no picker anywhere
 * for a human to choose it, so the hue was never a decision — it was noise
 * that happened to be deterministic, one of the assorted blue/pink/orange
 * sidebar icons the founder called out directly." That reasoning was
 * correct, and it is exactly what changes below — not a quiet revert of it.
 * Founder: "I would love to have some shapes like this that are customised,
 * with its colour as well — this is how it is in Linear."
 *
 * The distinction the old comment drew is still the live rule: a colour
 * assigned by HASHING the project id is decoration nobody chose. A colour a
 * PERSON picks, through ProjectIdentityPicker below, is information — red
 * for the urgent client, green for internal — even though it is stored in
 * the exact same `metadata.tint` column a hash used to fill blindly. Same
 * pixels, opposite meaning. The backend
 * (server_modules/projects_repository.py) still seeds every new project
 * with a deterministic icon/tint at creation — that part is UNCHANGED, and
 * is not itself a decision anyone has to make, any more than a generated
 * repo colour is on GitHub before you touch it. What's new is
 * projects_repository.set_project_identity, which lets a person overwrite
 * that seed (PATCH .../fleet/projects/{id}) through the picker below,
 * reachable from the New-project composer and ProjectSettings.tsx. DO NOT
 * read "there's still a deterministic default" as license to strip the
 * picker back out — the earlier objection was to a colour nobody could
 * ever choose, not to colour existing at all. If this file's tint handling
 * is ever touched again, keep the picker; that is the whole point of this
 * pass.
 *
 * ACCENT DISCIPLINE STILL HOLDS. TINTS (fleet-presentation.ts) is a
 * separate, 8-colour identity vocabulary — a project's chosen colour must
 * never read as, or collide with, `--accent` (the one reserved violet
 * fill, accent-restraint.test.ts). Nothing here ever references --accent;
 * every swatch below paints with a literal TINTS value via inline style.
 * Selection inside the picker is marked by a neutral ring (the same
 * technique task-labels.tsx's LabelColorSwatches already shipped for
 * exactly this problem — "select a colour without using colour to signal
 * the selection") plus a drawn checkmark on the colour grid, never by
 * being "the one that's coloured" — CLAUDE.md: "selection is weight and
 * shape, never hue."
 */
export const PROJECT_ICON_MAP: Record<string, LucideIcon> = {
  rocket: Rocket,
  target: Target,
  compass: Compass,
  flag: Flag,
  star: Star,
  zap: Zap,
  package: Package,
  briefcase: Briefcase,
  layers: Layers,
  box: Box,
  puzzle: Puzzle,
  shield: Shield,
  gem: Gem,
  anchor: Anchor,
  globe: Globe,
  flame: Flame,
  "folder-kanban": FolderKanban,
};

const DEFAULT_ICON_NAME = "folder-kanban";
const DEFAULT_ICON = FolderKanban;

/** The full, ordered colour vocabulary a person can choose from — every key
 *  TINTS defines, same order the object is authored in. Kept as its own
 *  constant (rather than every caller re-deriving it) so the picker's grid
 *  order can never drift from what TINTS itself declares. */
const TINT_CHOICES = Object.keys(TINTS) as TintKey[];

function isTintKey(value?: string | null): value is TintKey {
  return typeof value === "string" && Object.prototype.hasOwnProperty.call(TINTS, value);
}

export function projectIconComponent(iconName?: string | null): LucideIcon {
  return (iconName && PROJECT_ICON_MAP[iconName]) || DEFAULT_ICON;
}

/** The project's icon, rendered as a small tile — the one building block
 *  used everywhere a project appears (list rows, detail header,
 *  breadcrumbs, agent-list group headers, the rail) so it reads
 *  identically in all of them. `tint` now actually renders — see the file
 *  banner above — falling back to the plain neutral tile for an unset or
 *  unrecognised value rather than guessing or crashing (an old row from
 *  before this existed, or a hand-edited metadata blob, must still render
 *  a real tile, never a blank one). */
export function ProjectIcon({
  icon,
  tint,
  size = 28,
  glyphSize,
}: {
  icon?: string | null;
  tint?: string | null;
  size?: number;
  glyphSize?: number;
}) {
  const Icon = projectIconComponent(icon);
  const resolvedTint = isTintKey(tint) ? tint : undefined;
  return (
    <TintTile size={size} tint={resolvedTint}>
      <Icon size={glyphSize ?? Math.round(size * 0.55)} strokeWidth={1.75} />
    </TintTile>
  );
}

/**
 * Icon + colour picker. A small trigger — the project's own current tile,
 * made clickable — rather than a grid inlined into whatever form hosts it,
 * so it fits inside the compact New-project composer and the
 * ProjectSettings popover without growing either one (both are surfaces
 * that were deliberately kept small; a picker that doubles their height
 * would undo that). Opening it reveals two dense grids in one popover —
 * icon shapes, then colour swatches — airy between the two groups, dense
 * within each, matching every other menu popover in this product
 * (.fleet-composer-pop's own family) rather than inventing a new shell.
 *
 * `onChange` always receives BOTH fields — picking just the icon keeps the
 * caller's current tint (defaulting to the first TINT_CHOICES entry if the
 * project has none yet) and vice versa, so a caller's PATCH body never has
 * to guess at a value the picker itself didn't touch this click.
 *
 * `disabled` renders the plain read-only tile with no trigger affordance —
 * CLAUDE.md's "no dead controls": a viewer who cannot edit a project (every
 * caller gates this the same way ProjectSettings.tsx's own trigger is
 * gated, owner-only) never sees a button that would 403 if pressed.
 */
export function ProjectIdentityPicker({
  icon,
  tint,
  onChange,
  disabled,
  size = 28,
  triggerLabel = "Project icon and colour",
}: {
  icon?: string | null;
  tint?: string | null;
  onChange: (next: { icon: string; tint: TintKey }) => void;
  disabled?: boolean;
  size?: number;
  triggerLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // Only this popover closes — same "the menu closes, the surface
      // hosting it doesn't" contract TaskComposer's ChipMenu uses, load-
      // bearing here since ProjectSettings.tsx hosts this inside its own
      // dismissable popover and a shared Escape must not take both down
      // at once.
      e.stopPropagation();
      setOpen(false);
    };
    document.addEventListener("mousedown", onDown, true);
    window.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("mousedown", onDown, true);
      window.removeEventListener("keydown", onKeyDown, true);
    };
  }, [open]);

  const activeTint: TintKey = isTintKey(tint) ? tint : TINT_CHOICES[0];
  const activeIcon = icon && PROJECT_ICON_MAP[icon] ? icon : DEFAULT_ICON_NAME;

  if (disabled) {
    return <ProjectIcon icon={icon} tint={tint} size={size} />;
  }

  return (
    <div className="fleet-identity-picker" ref={wrapRef}>
      <button
        type="button"
        className="fleet-identity-picker-trigger"
        aria-haspopup="true"
        aria-expanded={open}
        aria-label={triggerLabel}
        title={triggerLabel}
        onClick={() => setOpen((v) => !v)}
      >
        <ProjectIcon icon={icon} tint={tint} size={size} />
      </button>
      {open ? (
        <div className="fleet-identity-picker-pop" role="dialog" aria-label={triggerLabel}>
          <div className="fleet-identity-picker-label">Icon</div>
          <div className="fleet-identity-swatch-grid" role="radiogroup" aria-label="Icon">
            {Object.keys(PROJECT_ICON_MAP).map((name) => {
              const Icon = PROJECT_ICON_MAP[name];
              const selected = name === activeIcon;
              return (
                <button
                  key={name}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  aria-label={name}
                  title={name}
                  className={`fleet-identity-swatch fleet-identity-swatch--icon${selected ? " is-selected" : ""}`}
                  onClick={() => onChange({ icon: name, tint: activeTint })}
                >
                  <Icon size={14} strokeWidth={1.75} />
                </button>
              );
            })}
          </div>

          <div className="fleet-identity-picker-divider" />

          <div className="fleet-identity-picker-label">Colour</div>
          <div className="fleet-identity-swatch-grid" role="radiogroup" aria-label="Colour">
            {TINT_CHOICES.map((k) => {
              const selected = k === activeTint;
              return (
                <button
                  key={k}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  aria-label={TINT_LABELS[k]}
                  title={TINT_LABELS[k]}
                  className={`fleet-identity-swatch fleet-identity-swatch--tint${selected ? " is-selected" : ""}`}
                  style={{ background: TINTS[k].fg }}
                  onClick={() => onChange({ icon: activeIcon, tint: k })}
                >
                  {/* Fixed dark literal, not a theme token: every TINTS.fg
                      value is a light/mid-brightness colour in both
                      themes (fleet-presentation.ts hardcodes the same hex
                      for light and dark), so a dark mark reads on all
                      eight without needing its own per-theme fork. */}
                  {selected ? <Check size={11} strokeWidth={2.75} color="rgba(10, 10, 14, 0.85)" /> : null}
                </button>
              );
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}

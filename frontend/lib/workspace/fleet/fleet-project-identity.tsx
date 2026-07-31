import {
  Anchor,
  Box,
  Briefcase,
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

/**
 * Project identity — an icon. The backend also assigns a deterministic
 * "tint" per project (server_modules/projects_repository.py, hashed from
 * the project id) — history: earlier UI rendered that as a per-project
 * background/glyph hue. Colour-discipline pass: dropped. There is no
 * picker anywhere for a human to choose it, so the hue was never a
 * decision — it was noise that happened to be deterministic, one of the
 * assorted blue/pink/orange sidebar icons the founder called out directly.
 * The icon shape still gives every project a real, glance-distinguishable
 * identity; it just reads in the neutral tile fill every other icon-tile in
 * the app uses (TintTile with no accent/danger). The backend field is left
 * alone — it's harmless, unused data, and ripping it out is a backend
 * migration for zero UI benefit.
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

const DEFAULT_ICON = FolderKanban;

export function projectIconComponent(iconName?: string | null): LucideIcon {
  return (iconName && PROJECT_ICON_MAP[iconName]) || DEFAULT_ICON;
}

/** The project's icon, rendered as a small neutral tile — the one building
 *  block used everywhere a project appears (list rows, detail header,
 *  breadcrumbs, agent-list group headers) so it reads identically in all of
 *  them. `tint` is still accepted (every caller still passes the project's
 *  backend-assigned tint through) but deliberately unused — see the file
 *  banner above. */
export function ProjectIcon({
  icon,
  tint: _tint,
  size = 28,
  glyphSize,
}: {
  icon?: string | null;
  tint?: string | null;
  size?: number;
  glyphSize?: number;
}) {
  const Icon = projectIconComponent(icon);
  return (
    <TintTile size={size}>
      <Icon size={glyphSize ?? Math.round(size * 0.55)} strokeWidth={1.75} />
    </TintTile>
  );
}

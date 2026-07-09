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
import { TINTS, type TintKey } from "./fleet-presentation";

/**
 * Project identity — icon + tint. The backend assigns both deterministically
 * from the project id at creation (server_modules/projects_repository.py)
 * and always returns a non-empty pair (falls back live for rows written
 * before this existed), so the frontend's only job is mapping the returned
 * name strings to the actual icon/color. Never render "Projects" — or a
 * project — with no visual identity.
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
const DEFAULT_TINT: TintKey = "blue";

export function projectIconComponent(iconName?: string | null): LucideIcon {
  return (iconName && PROJECT_ICON_MAP[iconName]) || DEFAULT_ICON;
}

export function projectTintKey(tint?: string | null): TintKey {
  return tint && tint in TINTS ? (tint as TintKey) : DEFAULT_TINT;
}

/** The project's icon+tint, rendered as a small tinted tile — the one
 *  building block used everywhere a project appears (list rows, detail
 *  header, breadcrumbs, agent-list group headers) so it reads identically
 *  in all of them. */
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
  return (
    <TintTile tint={projectTintKey(tint)} size={size}>
      <Icon size={glyphSize ?? Math.round(size * 0.55)} strokeWidth={1.75} />
    </TintTile>
  );
}

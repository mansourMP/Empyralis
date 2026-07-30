"use client";

// Overlapping member-avatar stack — Multiplayer Projects Phase 1 (MAN-114).
// "Project member" == "workspace member" for now (MAN-70 ruling, no
// per-project ACL table yet), so this renders the workspace's own member
// list. Hover reveals a name + role tooltip, plus real per-project
// attribution when it exists (how many tasks in THIS project this person
// created, and the most recent one) — never an invented "last reviewed by"
// claim; if a member has created no tasks here, the tooltip just shows name
// + role.
//
// Interaction reference the founder pasted was an overlapping-avatars +
// framer-motion hover-card component. This codebase already depends on
// "motion" (frontend/package.json:22, `motion/react` import) — Framer
// Motion's current package name, same API — and already has a small set of
// reusable motion primitives at lib/ui/motion.tsx (MotionInlineBanner uses
// the exact same AnimatePresence + fade-in pattern this tooltip needs), so
// this reuses that dependency and those transition tokens rather than adding
// a new one. There is no `cn` utility anywhere in this repo (grepped
// frontend/lib for one) — className joining here matches the local
// `joinClassNames` helper every other file in this directory already
// defines inline (see McpServersSection.tsx, lib/ui/primitives.tsx).
//
// Avatars are initials-in-a-circle, not photos — no member has a profile
// photo anywhere in this platform yet. This mirrors the existing identity-
// avatar convention exactly: PrimaryRail.tsx's owner avatar
// (`.fleet-rail-owner-avatar`, first-letter-of-name in a circle) and
// TasksList.tsx's tinted `.fleet-agent-avatar`. `.fleet-member-avatar` here
// is the same circular treatment, tinted per member via the same
// TINTS/tintKeyForIndex identity-color helper agents and projects already
// use.

import { useMemo, useState, type CSSProperties } from "react";
import { AnimatePresence, motion } from "motion/react";

import { TINTS, tintKeyForIndex } from "@/lib/workspace/fleet/fleet-presentation";
import { APP_MOTION_TRANSITIONS } from "@/lib/ui/motion";
import { useWorkspaceMembers, type WorkspaceMember, type WorkspaceRole } from "@/lib/workspace/fleet/members-data";
import type { FleetTask } from "@/lib/workspace/fleet/fleet-data";

function joinClassNames(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(" ");
}

function initials(label: string): string {
  const parts = label.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0].charAt(0) + parts[parts.length - 1].charAt(0)).toUpperCase();
}

function roleLabel(role: WorkspaceRole): string {
  if (role === "owner") return "Owner";
  if (role === "member") return "Member";
  return "Viewer";
}

// "xs" (18px) added for MAN-64/MAN-70's human-assignee avatars, which sit
// beside the 12-16px AgentSigil tiles on task cards/rows (TasksBoard,
// TasksList, TasksGroupedList) -- "sm" (26px) reads oversized next to those,
// where every existing caller here (the member roster stack, tooltips) has
// room for the larger sizes.
const SIZE_PX = { xs: 18, sm: 26, md: 32, lg: 40 } as const;

export function MemberAvatar({
  name,
  role,
  size = "md",
  tintIndex = 0,
  className,
}: {
  name: string;
  role?: WorkspaceRole;
  size?: "xs" | "sm" | "md" | "lg";
  tintIndex?: number;
  className?: string;
}) {
  const tint = TINTS[tintKeyForIndex(tintIndex)];
  const px = SIZE_PX[size];
  // Same "--tile-bg"/"--tile-fg" custom-property + cast pattern
  // TasksList.tsx's avatarStyle already uses for the identical tinted-circle
  // treatment on agent avatars.
  const style = {
    width: px,
    height: px,
    fontSize: size === "lg" ? 15 : size === "md" ? 13 : size === "sm" ? 11 : 9,
    "--tile-bg": tint.bg,
    "--tile-fg": tint.fg,
  } as CSSProperties;
  return (
    <span
      className={joinClassNames("fleet-member-avatar", className)}
      style={style}
      title={role ? `${name} · ${roleLabel(role)}` : name}
    >
      {initials(name)}
    </span>
  );
}

type MemberAttribution = { taskCount: number; lastTaskTitle: string | null };

/** Real, already-tracked attribution: project_tasks_service rows carry
 *  `created_by` (the human user id who created the task — see
 *  fleet-data.ts:963-964's own note that this "records the human author").
 *  Correlated against workspace members by user_id. Deliberately does NOT
 *  invent anything beyond what's actually in the tasks list passed in —
 *  no "last reviewed", no assignment (assignment is agent-only, never a
 *  human). Zero tasks created here means the tooltip shows nothing extra. */
function buildAttribution(tasks: FleetTask[] | undefined): Map<string, MemberAttribution> {
  const map = new Map<string, MemberAttribution>();
  if (!tasks) return map;
  // Oldest-to-newest so the last write wins and ends up "most recent".
  const ordered = [...tasks].sort((a, b) => {
    const ta = a.created_at ? new Date(a.created_at).getTime() : 0;
    const tb = b.created_at ? new Date(b.created_at).getTime() : 0;
    return ta - tb;
  });
  for (const task of ordered) {
    const createdBy = String(task.created_by || "").trim();
    if (!createdBy) continue;
    const prior = map.get(createdBy) || { taskCount: 0, lastTaskTitle: null };
    map.set(createdBy, {
      taskCount: prior.taskCount + 1,
      lastTaskTitle: task.title || prior.lastTaskTitle,
    });
  }
  return map;
}

function MemberTooltip({
  member,
  attribution,
}: {
  member: WorkspaceMember;
  attribution: MemberAttribution | undefined;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 4, scale: 0.98 }}
      transition={APP_MOTION_TRANSITIONS.fade}
      className="fleet-member-avatar-tooltip"
      role="tooltip"
    >
      <div className="fleet-member-avatar-tooltip-name">{member.display_name || member.email}</div>
      <div className="fleet-member-avatar-tooltip-role">{roleLabel(member.role)}</div>
      {attribution && attribution.taskCount > 0 ? (
        <div className="fleet-member-avatar-tooltip-attribution">
          Created {attribution.taskCount} {attribution.taskCount === 1 ? "task" : "tasks"} here
          {attribution.lastTaskTitle ? ` · latest "${attribution.lastTaskTitle}"` : ""}
        </div>
      ) : null}
    </motion.div>
  );
}

export function MemberAvatarStack({
  workspaceId,
  tasks,
  maxVisible = 5,
  size = "md",
}: {
  workspaceId: string;
  /** This project's own tasks (already scoped by the caller via
   *  useFleetTasks(workspaceId, projectId)) — used only to compute real
   *  per-member attribution, never fetched independently by this component. */
  tasks?: FleetTask[];
  maxVisible?: number;
  size?: "sm" | "md" | "lg";
}) {
  const { members, loading } = useWorkspaceMembers(workspaceId);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const attribution = useMemo(() => buildAttribution(tasks), [tasks]);

  if (loading || members.length === 0) return null;

  const visible = members.slice(0, maxVisible);
  const overflow = members.length - visible.length;

  return (
    <div className="fleet-member-avatar-stack" role="group" aria-label="Project members">
      {visible.map((m, i) => (
        <div
          key={m.user_id}
          className="fleet-member-avatar-stack-item"
          style={{ zIndex: visible.length - i }}
          onMouseEnter={() => setHoveredId(m.user_id)}
          onMouseLeave={() => setHoveredId((cur) => (cur === m.user_id ? null : cur))}
        >
          <AnimatePresence>
            {hoveredId === m.user_id ? (
              <MemberTooltip member={m} attribution={attribution.get(m.user_id)} />
            ) : null}
          </AnimatePresence>
          <MemberAvatar name={m.display_name || m.email} role={m.role} size={size} tintIndex={i} />
        </div>
      ))}
      {overflow > 0 ? (
        <div className="fleet-member-avatar-stack-item" style={{ zIndex: 0 }}>
          <span
            className="fleet-member-avatar fleet-member-avatar--overflow"
            style={{ width: SIZE_PX[size], height: SIZE_PX[size], fontSize: size === "lg" ? 13 : 11 }}
            title={`${overflow} more ${overflow === 1 ? "member" : "members"}`}
          >
            +{overflow}
          </span>
        </div>
      ) : null}
    </div>
  );
}

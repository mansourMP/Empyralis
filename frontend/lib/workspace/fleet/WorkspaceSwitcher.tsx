"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Check, ChevronsUpDown, Plus } from "lucide-react";

import { useAccountShell } from "@/lib/shell/account-shell-context";

const ROLE_LABEL: Record<string, string> = {
  owner: "Owner",
  member: "Member",
  viewer: "Viewer",
};

/**
 * Workspace switcher — MAN-108 Phase 1 bug 4. Server-side, an invited member's
 * membership in the inviting owner's workspace was already correct the moment
 * they accepted (role, permissions, data visibility all check out); what was
 * missing was any UI path to actually get there. The account shell already
 * loads every workspace the signed-in user belongs to
 * (state.workspaceMemberships, from GET /api/auth/account-shell — the same
 * membership list GET /api/workspaces serves) and already knows how to route
 * into one (actions.resolveWorkspaceHref — last-visited route if any,
 * otherwise the membership's own defaultRoute). This just gives that data an
 * actual control: the workspace brand mark/name in the rail header, which
 * used to be inert text, is now the switcher's trigger — the natural spot,
 * since it's the one element that already names "where you are."
 */
export function WorkspaceSwitcher({
  workspaceId,
  workspaceName,
  collapsed,
}: {
  workspaceId: string;
  workspaceName: string;
  collapsed: boolean;
}) {
  const router = useRouter();
  const { state, actions } = useAccountShell();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (ref.current?.contains(event.target as Node)) return;
      setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const memberships = state.workspaceMemberships;
  const hasOtherWorkspaces = memberships.some((membership) => membership.workspace.id !== workspaceId);

  function goTo(targetWorkspaceId: string) {
    setOpen(false);
    if (targetWorkspaceId === workspaceId) return;
    const membership = memberships.find((m) => m.workspace.id === targetWorkspaceId);
    // resolveWorkspaceHref returns the member's last-visited route inside that
    // workspace when we have one on file, so switching back to a workspace
    // drops you where you left off rather than always resetting to Sage.
    const href =
      actions.resolveWorkspaceHref(targetWorkspaceId)
      ?? membership?.defaultRoute
      ?? `/w/${encodeURIComponent(targetWorkspaceId)}/sage`;
    router.push(href);
  }

  const brandLetter = (workspaceName || "E").charAt(0).toUpperCase();

  return (
    <div className="fleet-rail-workspace-switcher" ref={ref}>
      {open && <div className="fleet-rail-popover-backdrop" onClick={() => setOpen(false)} />}
      {open && (
        <div className="fleet-rail-workspace-popover" role="menu" aria-label="Switch workspace">
          {memberships.map((membership) => {
            const active = membership.workspace.id === workspaceId;
            const label = membership.workspace.label || membership.workspace.id;
            return (
              <button
                key={membership.workspace.id}
                type="button"
                role="menuitemradio"
                aria-checked={active}
                className={`fleet-rail-workspace-popover-row${active ? " is-active" : ""}`}
                onClick={() => goTo(membership.workspace.id)}
              >
                <span className="fleet-rail-workspace-popover-mark">{label.charAt(0).toUpperCase()}</span>
                <span className="fleet-rail-workspace-popover-text">
                  <span className="fleet-rail-workspace-popover-name">{label}</span>
                  <span className="fleet-rail-workspace-popover-role">
                    {ROLE_LABEL[membership.role] ?? membership.role}
                  </span>
                </span>
                {active && <Check size={14} strokeWidth={2} aria-hidden="true" />}
              </button>
            );
          })}
          <Link
            href="/workspaces/new"
            role="menuitem"
            className="fleet-rail-workspace-popover-row fleet-rail-workspace-popover-row--add"
            onClick={() => setOpen(false)}
          >
            <span className="fleet-rail-workspace-popover-mark fleet-rail-workspace-popover-mark--add">
              <Plus size={12} strokeWidth={2} aria-hidden="true" />
            </span>
            <span className="fleet-rail-workspace-popover-text">
              <span className="fleet-rail-workspace-popover-name">New workspace</span>
            </span>
          </Link>
        </div>
      )}
      <button
        type="button"
        className="fleet-rail-workspace-trigger"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        // Was conditional on `collapsed` — expanded relied on the visible
        // "{brandLetter}{workspaceName}" content to name the button, which
        // the live a11y tree (MAN-145 item 5) showed coming through empty.
        // Unconditional aria-label is the unambiguous fix in both states.
        aria-label={`Switch workspace (current: ${workspaceName})`}
        title={collapsed ? workspaceName : undefined}
      >
        <span className="fleet-rail-brand-mark">{brandLetter}</span>
        {!collapsed && (
          <span className="fleet-rail-workspace-name" title={workspaceName}>
            {workspaceName}
          </span>
        )}
        {!collapsed && hasOtherWorkspaces && (
          <ChevronsUpDown size={12} strokeWidth={1.75} className="fleet-rail-workspace-chevron" aria-hidden="true" />
        )}
      </button>
    </div>
  );
}

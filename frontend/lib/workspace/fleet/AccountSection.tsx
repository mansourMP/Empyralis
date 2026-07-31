"use client";

import { UserCog } from "lucide-react";

/**
 * Account — currently empty. Exists for two reasons: it's the destination
 * `/settings/account` (app/(account)/settings/account/page.tsx →
 * resolve-settings-route.ts) has pointed at since it was written, with
 * nothing ever there to land on; and it's the home future personal (not
 * workspace-wide) preferences get instead of being bolted onto Workspace.
 * No workspace-scoped control belongs here — those are Workspace/
 * Connections. An honest empty state, not a stub page pretending to be a
 * feature (CLAUDE.md's "no dead controls" — there's nothing to click yet,
 * so nothing renders as clickable). */
export function AccountSection() {
  return (
    <>
      <h2 className="fleet-detail-section-title">Account</h2>
      <div className="fleet-empty">
        <div className="fleet-empty-icon">
          <UserCog size={20} strokeWidth={1.75} />
        </div>
        <div className="fleet-empty-title">Nothing here yet</div>
        <div className="fleet-empty-desc">
          Personal preferences — the ones that follow you, not the workspace — will live here.
        </div>
      </div>
    </>
  );
}

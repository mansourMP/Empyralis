"use client";

import { useEffect, useState } from "react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { useFleetWorkspace } from "@/lib/workspace/fleet/fleet-data";

/** Workspace display name — PATCH /api/workspaces/{id} already accepted
 *  `name` (routes_workspaces.py's update_workspace) with no UI anywhere to
 *  reach it. Same fetch-on-mount + refresh() idiom as EmergencyStopSection
 *  (useFleetWorkspace), so this section owns its own load state independent
 *  of the rest of the page. Extracted from settings/page.tsx unchanged when
 *  Settings moved to a grouped rail — this now renders inside the
 *  "Workspace" section only, not stacked with every other section. */
export function WorkspaceNameSection({ workspaceId }: { workspaceId: string }) {
  const { workspace, loading, refresh } = useFleetWorkspace(workspaceId);
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Seed the input from the loaded workspace, but only once it actually
  // changes — never stomp a name the owner is mid-typing on a background
  // refresh (e.g. the Emergency Stop section's own refresh() firing).
  useEffect(() => {
    if (workspace?.name) setName(workspace.name);
  }, [workspace?.name]);

  const trimmed = name.trim();
  const dirty = trimmed.length > 0 && trimmed !== workspace?.name;

  async function handleSave() {
    if (!trimmed) {
      setError("Workspace name cannot be empty.");
      return;
    }
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const res = await fetch(`/api/workspaces/${encodeURIComponent(workspaceId)}`, {
        method: "PATCH",
        credentials: "include",
        headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
        body: JSON.stringify({ name: trimmed }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.detail || `Could not rename workspace (HTTP ${res.status})`);
      setSaved(true);
      await refresh();
      // The rail's own workspace-name source is a separate server-rendered
      // fetch (loadAccountShellSession), not this hook — reload is the
      // simplest way to guarantee the rail, breadcrumb root, and every other
      // reader pick up the new name immediately instead of drifting stale
      // until the next hard navigation.
      window.location.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not rename workspace.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <h2 className="fleet-detail-section-title">Workspace name</h2>
      <p className="fleet-subtitle" style={{ marginTop: 0 }}>
        Shown in the rail, breadcrumbs, and anywhere else this workspace is referenced.
      </p>
      {error ? <div className="fleet-page-state-body" role="alert" style={{ color: "var(--offline-text)" }}>{error}</div> : null}
      <div style={{ display: "flex", gap: "var(--space-2)", alignItems: "center", flexWrap: "wrap", marginBottom: "var(--space-6)" }}>
        <input
          className="fleet-wizard-input"
          value={name}
          onChange={(e) => { setName(e.target.value); setSaved(false); }}
          placeholder="Workspace name"
          disabled={loading || saving}
          style={{ flex: "1 1 260px" }}
        />
        <button
          type="button"
          className="fleet-btn fleet-btn--accent"
          onClick={handleSave}
          disabled={saving || loading || !dirty}
        >
          {saving ? "Saving…" : saved ? "Saved" : "Save"}
        </button>
      </div>
    </>
  );
}

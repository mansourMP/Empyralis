"use client";

import { useCallback, useEffect, useState } from "react";
import { FileText, Loader2, Trash2 } from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import type { FleetAgent } from "../fleet-data";

/**
 * MEMORY tab — the Phase 6 memory tree. MEMORY.md is the index and is shown
 * first; topic files follow. The owner can read, edit (PUT), and delete a file.
 * Paths are server-hardened, so we pass them through as-is.
 */

type TreeFile = { path: string; chars?: number };

export function MemoryTab({
  workspaceId,
  agentId,
}: {
  workspaceId: string;
  agentId: string;
  agent?: FleetAgent | null;
  onChat?: () => void;
}) {
  const apiBase = `/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}/memory`;
  const [files, setFiles] = useState<TreeFile[]>([]);
  const [indexPath, setIndexPath] = useState<string>("MEMORY.md");
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [original, setOriginal] = useState("");
  const [fileLoading, setFileLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadTree = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${apiBase}/tree`, { credentials: "include" });
      const d = await res.json().catch(() => ({}));
      const idxPath = String(d?.index?.path || "MEMORY.md");
      setIndexPath(idxPath);
      const idx: TreeFile[] = d?.index?.path ? [{ path: idxPath, chars: d.index.chars }] : [];
      const topics: TreeFile[] = Array.isArray(d?.topics)
        ? d.topics.map((t: any) => ({ path: String(t?.path || t?.name || t), chars: t?.chars }))
        : [];
      // MEMORY.md (index) first, then topics, de-duplicated.
      const all = [...idx, ...topics.filter((t) => t.path && t.path !== idxPath)];
      setFiles(all);
    } catch {
      setError("Could not load memory.");
    } finally {
      setLoading(false);
    }
  }, [apiBase]);

  useEffect(() => { loadTree(); }, [loadTree]);

  const openFile = useCallback(async (path: string) => {
    setSelected(path);
    setFileLoading(true);
    setError(null);
    try {
      const res = await fetch(`${apiBase}/file?path=${encodeURIComponent(path)}`, { credentials: "include" });
      const d = await res.json().catch(() => ({}));
      const c = String(d?.content ?? d?.text ?? "");
      setContent(c);
      setOriginal(c);
    } catch {
      setError("Could not open that file.");
    } finally {
      setFileLoading(false);
    }
  }, [apiBase]);

  // Open the index (MEMORY.md) first once the tree loads.
  useEffect(() => {
    if (!selected && files.length > 0) openFile(files[0].path);
  }, [files, selected, openFile]);

  async function save() {
    if (!selected) return;
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`${apiBase}/file?path=${encodeURIComponent(selected)}`, {
        method: "PUT",
        credentials: "include",
        headers: buildCookieAuthHeaders("PUT", { "Content-Type": "application/json" }),
        body: JSON.stringify({ content }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok || d?.ok === false) throw new Error(d?.error || d?.detail || `HTTP ${res.status}`);
      setOriginal(content);
      loadTree();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed.");
    } finally {
      setSaving(false);
    }
  }

  async function del() {
    if (!selected) return;
    if (typeof window !== "undefined" && !window.confirm(`Delete ${selected}?`)) return;
    setError(null);
    try {
      const res = await fetch(`${apiBase}/file?path=${encodeURIComponent(selected)}`, {
        method: "DELETE",
        credentials: "include",
        headers: buildCookieAuthHeaders("DELETE", {}),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setSelected(null);
      setContent("");
      setOriginal("");
      loadTree();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed.");
    }
  }

  const dirty = content !== original;

  if (loading) {
    return <div className="fleet-detail-pad"><div className="fleet-page-state-body">Loading memory…</div></div>;
  }

  return (
    <div className="fleet-memory-browser">
      <div className="fleet-memory-file-list">
        {files.map((f) => (
          <button
            key={f.path}
            type="button"
            className={`fleet-memory-file-item${selected === f.path ? " is-active" : ""}`}
            onClick={() => openFile(f.path)}
          >
            <FileText size={13} strokeWidth={1.75} />
            <span>{f.path}{f.path === indexPath ? "  · index" : ""}</span>
          </button>
        ))}
        {files.length === 0 && <div className="fleet-page-state-body">No memory files yet.</div>}
      </div>
      <div className="fleet-memory-editor">
        {selected ? (
          <>
            <div className="fleet-memory-editor-header">
              <span>{selected}</span>
              <div style={{ display: "flex", gap: 8 }}>
                <button type="button" className="fleet-btn" onClick={del} aria-label="Delete file">
                  <Trash2 size={13} strokeWidth={1.75} />
                </button>
                <button type="button" className="fleet-btn fleet-btn--accent" onClick={save} disabled={!dirty || saving}>
                  {saving ? <Loader2 size={13} style={{ animation: "spin 1s linear infinite" }} /> : "Save"}
                </button>
              </div>
            </div>
            {fileLoading ? (
              <div className="fleet-page-state-body">Loading…</div>
            ) : (
              <textarea
                className="fleet-memory-editor-textarea"
                value={content}
                onChange={(e) => setContent(e.currentTarget.value)}
                spellCheck={false}
              />
            )}
          </>
        ) : (
          <div className="fleet-page-state-body">Select a file to read or edit it.</div>
        )}
        {error && <div className="fleet-channel-expand-error">{error}</div>}
      </div>
    </div>
  );
}

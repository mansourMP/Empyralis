"use client";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { useCallback, useEffect, useMemo, useState } from "react";
import { FileText, Info, Loader2, Trash2 } from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { parseMarkdownLiteBlocks, renderMarkdownLiteInline } from "@/lib/workspace/markdown-lite";
import type { FleetAgent } from "../fleet-data";

/**
 * MEMORY tab — the Phase 6 memory tree. MEMORY.md is the index and is shown
 * first; topic files follow. The owner can read, edit (PUT), and delete a file.
 * Paths are server-hardened, so we pass them through as-is.
 */

type TreeFile = { path: string; size?: number };

// ── Rendering: MEMORY.md and topic files are hand-authored markdown-ish text
// (see workspace_context.py's DEFAULT_CONTEXT_FILE_CONTENTS), not full CommonMark.
// Every scaffold starts with a `---`-fenced "Purpose:" blob that's hard-wrapped
// at ~78 columns in the source file — rendered verbatim in a monospace box that
// wrap width doesn't match, those hard breaks read as a cramped, run-together
// mess (the exact "looks broken" the owner flagged). Splitting that fence out
// and reflowing it into one real paragraph (browser-wrapped, not source-wrapped)
// fixes that at the render layer without touching the seeded content itself.
// The rest of the body reuses the same block parser chat turns already use
// (markdown-lite.tsx) so bold/italic/code/links/lists render instead of
// showing as literal punctuation, plus light heading detection on top.

type MemBlock =
  | { type: "meta"; text: string }
  | { type: "h1" | "h2" | "h3"; text: string }
  | { type: "p"; text: string }
  | { type: "ul" | "ol"; items: string[] };

function splitFrontmatter(raw: string): { meta: string | null; body: string } {
  const text = String(raw || "").replace(/\r\n/g, "\n");
  const lines = text.split("\n");
  if (lines[0]?.trim() !== "---") return { meta: null, body: text };
  let i = 1;
  const metaLines: string[] = [];
  while (i < lines.length && lines[i].trim() !== "---") {
    metaLines.push(lines[i]);
    i++;
  }
  if (i >= lines.length) return { meta: null, body: text }; // no closing fence — not real frontmatter
  const meta = metaLines.join(" ").replace(/\s+/g, " ").trim();
  const body = lines.slice(i + 1).join("\n").replace(/^\n+/, "");
  return { meta: meta || null, body };
}

function parseMemoryBlocks(raw: string): MemBlock[] {
  const { meta, body } = splitFrontmatter(raw);
  const blocks: MemBlock[] = [];
  if (meta) blocks.push({ type: "meta", text: meta });
  for (const b of parseMarkdownLiteBlocks(body)) {
    if (b.type === "ul" || b.type === "ol") {
      blocks.push({ type: b.type, items: b.items || [] });
      continue;
    }
    const text = b.text || "";
    // Only a single-line block can be a heading — a multi-line paragraph that
    // happens to start with "#" (rare, but possible in freeform topic notes)
    // stays a paragraph rather than swallowing its own continuation lines.
    const h = !text.includes("\n") ? /^(#{1,3})\s+(.*)$/.exec(text.trim()) : null;
    if (h) {
      blocks.push({ type: (`h${h[1].length}` as "h1" | "h2" | "h3"), text: h[2].trim() });
    } else if (text) {
      blocks.push({ type: "p", text });
    }
  }
  return blocks;
}

/** Exported so the Ask AI console's Memory view renders memory exactly the
 *  way this tab does — same parser, same blocks, same classes. There is one
 *  notion of "memory" in this product, not two. */
export function MemoryPreview({ content }: { content: string }) {
  const blocks = useMemo(() => parseMemoryBlocks(content), [content]);
  if (blocks.length === 0) {
    return <div className="fleet-memory-preview-empty">Empty — nothing written here yet.</div>;
  }
  return (
    <div className="fleet-memory-preview">
      {blocks.map((b, i) => {
        switch (b.type) {
          case "meta":
            return <p key={i} className="fleet-memory-preview-meta">{renderMarkdownLiteInline(b.text, `m${i}`)}</p>;
          case "h1":
            return <h3 key={i} className="fleet-memory-preview-heading">{renderMarkdownLiteInline(b.text, `h${i}`)}</h3>;
          case "h2":
          case "h3":
            return (
              <h4 key={i} className="fleet-memory-preview-heading fleet-memory-preview-heading--sub">
                {renderMarkdownLiteInline(b.text, `h${i}`)}
              </h4>
            );
          case "ul":
          case "ol": {
            const ListTag = b.type;
            return (
              <ListTag key={i} className="fleet-memory-preview-list">
                {b.items.map((item, ii) => (
                  <li key={ii}>{renderMarkdownLiteInline(item, `${i}-${ii}`)}</li>
                ))}
              </ListTag>
            );
          }
          default:
            return <p key={i} className="fleet-memory-preview-p">{renderMarkdownLiteInline(b.text, `p${i}`)}</p>;
        }
      })}
    </div>
  );
}

function formatMemSize(n?: number): string | null {
  if (typeof n !== "number" || !Number.isFinite(n) || n <= 0) return null;
  if (n < 1000) return `${Math.round(n)} B`;
  if (n < 1_000_000) return `${(n / 1000).toFixed(1).replace(/\.0$/, "")} KB`;
  return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, "")} MB`;
}

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
  const [isDefault, setIsDefault] = useState(false);
  const [fileLoading, setFileLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"preview" | "edit">("preview");

  // Agent identity guard — this tab can stay mounted across an agent switch
  // (command palette / Back nav swap the agentId prop without unmounting it,
  // same as every other fleet tab), so any state describing "which file is
  // open" has to be re-keyed off agentId or it survives the switch. Left
  // unguarded: `selected`/`content`/`original` from agent A linger after
  // apiBase has already moved on to agent B, so a later save() PUTs A's
  // (possibly-dirty) content at B's file path — cross-agent memory
  // corruption. Mirrors WorkTab keying its thread loader off a url that
  // embeds agentId (tabs/WorkTab.tsx); here the reset is explicit since
  // selected/content/original/isDefault have no url of their own to key off.
  useEffect(() => {
    setSelected(null);
    setContent("");
    setOriginal("");
    setIsDefault(false);
    setMode("preview");
  }, [agentId]);

  // `silent` skips the loading flag — used by save()/del() below to refresh
  // the tree AFTER a mutation. Without this, `setLoading(true)` unmounted
  // the whole two-pane file list + editor (replaced by the loading
  // skeleton) on every routine Save or Delete click, discarding whatever
  // was on screen — including the content just saved — for the length of
  // the refetch, then snapping back. The initial mount load (the effect
  // below) is the only caller that still wants the skeleton.
  const loadTree = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const res = await fleetAuthorizedFetch(`${apiBase}/tree`, { credentials: "include" });
      const d = await res.json().catch(() => ({}));
      const idxPath = String(d?.index?.path || "MEMORY.md");
      setIndexPath(idxPath);
      const idx: TreeFile[] = d?.index?.path ? [{ path: idxPath, size: d.index.chars }] : [];
      const topics: TreeFile[] = Array.isArray(d?.topics)
        ? d.topics.map((t: any) => ({ path: String(t?.path || t?.name || t), size: t?.chars ?? t?.size }))
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
    setMode("preview");
    setFileLoading(true);
    setError(null);
    try {
      const res = await fleetAuthorizedFetch(`${apiBase}/file?path=${encodeURIComponent(path)}`, { credentials: "include" });
      const d = await res.json().catch(() => ({}));
      const c = String(d?.content ?? d?.text ?? "");
      setContent(c);
      setOriginal(c);
      setIsDefault(Boolean(d?.is_default));
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
      const res = await fleetAuthorizedFetch(`${apiBase}/file?path=${encodeURIComponent(selected)}`, {
        method: "PUT",
        credentials: "include",
        headers: buildCookieAuthHeaders("PUT", { "Content-Type": "application/json" }),
        body: JSON.stringify({ content }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok || d?.ok === false) throw new Error(d?.error || d?.detail || `HTTP ${res.status}`);
      setOriginal(content);
      // The owner just wrote this content, so it's real regardless of what
      // it happens to say — never re-flag as the seeded scaffold.
      setIsDefault(false);
      loadTree(true);
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
      const res = await fleetAuthorizedFetch(`${apiBase}/file?path=${encodeURIComponent(selected)}`, {
        method: "DELETE",
        credentials: "include",
        headers: buildCookieAuthHeaders("DELETE", {}),
      });
      // The backend refuses to delete core files (MEMORY.md, HEARTBEAT.md, …) by
      // raising server-side, which routes_fleet.py's delete handler catches
      // and turns into {ok:false, error} at HTTP 200 — checking res.ok alone
      // (as this used to) reads that as success and shows "deleted" for a
      // file that's still there. Same ok===false parse as save() above.
      const d = await res.json().catch(() => ({}));
      if (!res.ok || d?.ok === false) throw new Error(d?.error || d?.detail || `HTTP ${res.status}`);
      setSelected(null);
      setContent("");
      setOriginal("");
      setIsDefault(false);
      loadTree(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed.");
    }
  }

  const dirty = content !== original;
  const topicCount = Math.max(0, files.length - 1);

  if (loading) {
    // Was a bare "Loading memory…" line alone in what .fleet-memory-browser's
    // CSS reserves as a full-height two-pane split (file list + editor) —
    // the tiny-text-in-a-big-area shape the loading-state audit flagged.
    // Reuses the real two-pane classes so the placeholder is the same shape
    // as what replaces it, never a separate width/layout to keep in sync.
    return (
      <div className="fleet-memory-browser" aria-busy="true" aria-label="Loading memory">
        <div className="fleet-memory-file-list">
          <div className="fleet-memory-file-list-title">Memory files</div>
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="fleet-memory-file-item" style={{ cursor: "default" }}>
              <div className="fleet-skeleton-bar" style={{ width: 13, height: 13 }} />
              <div className="fleet-skeleton-bar" style={{ width: `${60 - i * 8}%`, height: 11 }} />
            </div>
          ))}
        </div>
        <div className="fleet-memory-editor">
          <div className="fleet-memory-editor-header">
            <div className="fleet-skeleton-bar" style={{ width: 140, height: 11 }} />
            {/* The real header is `justify-content: space-between`: a
                filename on the left, a Preview/Edit segmented control plus
                Delete/Save buttons on the right — taller than the single
                thin bar this used to be alone, so the header grew the
                instant those controls appeared. */}
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div className="fleet-skeleton-bar" style={{ width: 90, height: 24, borderRadius: 6 }} />
              <div className="fleet-skeleton-bar" style={{ width: 26, height: 24, borderRadius: 6 }} />
              <div className="fleet-skeleton-bar" style={{ width: 54, height: 24, borderRadius: 6 }} />
            </div>
          </div>
          <div className="fleet-memory-preview-scroll">
            {[92, 84, 96, 40, 88, 70].map((w, i) => (
              <div key={i} className="fleet-skeleton-bar" style={{ width: `${w}%`, height: 11, marginBottom: 10 }} />
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="fleet-memory-browser">
      <div className="fleet-memory-file-list">
        <div className="fleet-memory-file-list-title">
          Memory files
          {files.length > 0 && <span className="fleet-memory-file-list-count">{files.length}</span>}
        </div>
        {files.map((f) => {
          const sizeLabel = f.path === indexPath ? null : formatMemSize(f.size);
          return (
            <button
              key={f.path}
              type="button"
              className={`fleet-memory-file-item${selected === f.path ? " is-active" : ""}`}
              onClick={() => openFile(f.path)}
            >
              <FileText size={13} strokeWidth={1.75} />
              <span className="fleet-memory-file-item-path">{f.path}</span>
              {f.path === indexPath && <span className="fleet-badge fleet-memory-file-item-badge">index</span>}
              {sizeLabel && <span className="fleet-memory-file-item-size">{sizeLabel}</span>}
            </button>
          );
        })}
        {files.length === 0 && <div className="fleet-page-state-body">No memory files yet.</div>}
        {topicCount === 0 && files.length > 0 && (
          <div className="fleet-memory-file-list-hint">
            The agent creates topic files here as it learns things worth remembering.
          </div>
        )}
      </div>
      <div className="fleet-memory-editor">
        {selected ? (
          <>
            <div className="fleet-memory-editor-header">
              <span className="fleet-memory-editor-filename">{selected}</span>
              <div className="fleet-memory-editor-actions">
                <div className="fleet-segmented" role="tablist" aria-label="View mode">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={mode === "preview"}
                    className={`fleet-segmented-btn${mode === "preview" ? " fleet-segmented-btn--active" : ""}`}
                    onClick={() => setMode("preview")}
                  >
                    Preview
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={mode === "edit"}
                    className={`fleet-segmented-btn${mode === "edit" ? " fleet-segmented-btn--active" : ""}`}
                    onClick={() => setMode("edit")}
                  >
                    Edit
                  </button>
                </div>
                <button
                  type="button"
                  className="fleet-btn"
                  onClick={del}
                  disabled={selected === indexPath}
                  aria-label="Delete file"
                  title={selected === indexPath ? "The memory index can't be deleted, only edited" : "Delete file"}
                >
                  <Trash2 size={13} strokeWidth={1.75} />
                </button>
                <button type="button" className="fleet-btn fleet-btn--accent" onClick={save} disabled={!dirty || saving}>
                  {saving ? <Loader2 size={13} style={{ animation: "spin 1s linear infinite" }} /> : "Save"}
                </button>
              </div>
            </div>
            {fileLoading ? (
              <div className="fleet-memory-preview-scroll" aria-busy="true" aria-label="Loading file">
                {[92, 84, 96, 40, 88, 70].map((w, i) => (
                  <div key={i} className="fleet-skeleton-bar" style={{ width: `${w}%`, height: 11, marginBottom: 10 }} />
                ))}
              </div>
            ) : (
              <>
                {isDefault && (
                  <div className="fleet-memory-scaffold-note">
                    <Info size={13} strokeWidth={1.75} />
                    <span>Starter scaffold — nobody has written to this file yet. This is Empyralis&apos; default template, not saved content.</span>
                  </div>
                )}
                {mode === "preview" ? (
                  <div className="fleet-memory-preview-scroll">
                    <MemoryPreview content={content} />
                  </div>
                ) : (
                  <textarea
                    className="fleet-memory-editor-textarea"
                    value={content}
                    onChange={(e) => setContent(e.currentTarget.value)}
                    spellCheck={false}
                    autoFocus
                  />
                )}
              </>
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

"use client";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { useCallback, useEffect, useMemo, useState } from "react";
import { CalendarClock, FileText, Info, Loader2, Lock, Sparkles, Trash2 } from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { parseMarkdownLiteBlocks, renderMarkdownLiteInline } from "@/lib/workspace/markdown-lite";
import type { FleetAgent } from "../fleet-data";

/**
 * MEMORY tab — everything this agent actually remembers, in one picker.
 *
 * There are FOUR real stores behind an agent's memory and until 2026-08-20
 * this surface showed exactly one of them. The other three were live, were
 * feeding the model's own prompt, and were unreachable from any screen:
 *
 *   Memory files   MEMORY.md index + memory/files/**.md topic files.
 *                  Server-side, on the backend's own disk. The original
 *                  contents of this tab; read/edit/delete, unchanged.
 *   Facts          the memory_entries key/value table — what the agent's
 *                  own `memory_write` tool writes. Carries attribution
 *                  (trust_tier), so a fact a stranger told it on a channel
 *                  is visibly different from one the owner stated.
 *   Recent activity the rolling day-log. NOT a log viewer: this exact text
 *                  is re-injected into the next turn as "Recent Daily
 *                  Logs", so it is what the agent will read about itself
 *                  tomorrow.
 *   Your note      the per-PERSON private note (Postgres,
 *                  agent_private_memory_notes). Scoped to the signed-in
 *                  caller by the server, which takes no user id from this
 *                  client at all — a teammate's note is not addressable
 *                  from here, by construction rather than by filtering.
 *
 * They share ONE left-hand picker rather than growing a second tab strip:
 * the rail is where you pick, the pane is what you picked.
 *
 * Paths are server-hardened, so we pass them through as-is.
 */

type TreeFile = { path: string; size?: number };

/** A pseudo-path for one of the three non-file stores. Kept in the same
 *  `selected` slot as a real file path so there is one selection model and
 *  one active-item rule, never two competing ones. The `__` prefix cannot
 *  collide with a real tree path: the server rejects any path that is not a
 *  markdown file at most one subdirectory deep. */
const FACTS_VIEW = "__facts__";
const DAILY_VIEW = "__daily__";
const PRIVATE_VIEW = "__private__";
const PSEUDO_VIEWS: ReadonlySet<string> = new Set([FACTS_VIEW, DAILY_VIEW, PRIVATE_VIEW]);

type MemoryFact = {
  key: string;
  content: string;
  updated_at?: number;
  trust_tier?: string;
  source_sender_name?: string | null;
  source_platform?: string | null;
};

/** The four trust tiers derive_trust_tier (agent_memory.py) can return,
 *  turned into the shortest honest label. "agent_inferred" is the legacy/
 *  no-attribution case and is the common one, so it says the plain truth
 *  ("no source recorded") instead of something that sounds like a verdict. */
function trustLabel(tier?: string, senderName?: string | null): string | null {
  switch (String(tier || "")) {
    case "owner":
      return "from you";
    case "non_owner_sender":
      return senderName ? `from ${senderName}` : "from someone else";
    case "unverified":
      return senderName ? `from ${senderName} (unverified)` : "unverified source";
    case "agent_inferred":
      return "no source recorded";
    default:
      return null;
  }
}

function formatFactTime(seconds?: number): string | null {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds <= 0) return null;
  try {
    return new Date(seconds * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch {
    return null;
  }
}

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

/** Flattens a (possibly nested) parsed list back to the flat item list this
 *  preview has always rendered — .fleet-memory-preview-list has no nested
 *  variant, and a memory file's indented sub-bullets read fine as siblings
 *  here. The shared renderer keeps the nesting; this surface doesn't use it. */
function flattenListItems(items: { text: string; children?: { items: { text: string; children?: unknown }[] }[] }[]): string[] {
  const out: string[] = [];
  for (const item of items) {
    out.push(item.text);
    for (const child of item.children ?? []) {
      out.push(...flattenListItems(child.items as never));
    }
  }
  return out;
}

function parseMemoryBlocks(raw: string): MemBlock[] {
  const { meta, body } = splitFrontmatter(raw);
  const blocks: MemBlock[] = [];
  if (meta) blocks.push({ type: "meta", text: meta });
  // parseMarkdownLiteBlocks is the app's ONE markdown block parser (see
  // lib/workspace/markdown-lite.tsx) — this tab maps its block tree onto its
  // own three-level preview presentation rather than re-parsing the text a
  // second way. Constructs the memory preview has no design for (fenced
  // code, tables, rules) fall through to a plain paragraph of their own
  // text, which is exactly what this surface showed before headings and
  // tables were parsed at all.
  for (const b of parseMarkdownLiteBlocks(body)) {
    switch (b.kind) {
      case "list":
        blocks.push({ type: b.ordered ? "ol" : "ul", items: flattenListItems(b.items) });
        break;
      case "heading":
        blocks.push({ type: (`h${Math.min(b.level, 3)}` as "h1" | "h2" | "h3"), text: b.text.trim() });
        break;
      case "code":
        if (b.code) blocks.push({ type: "p", text: b.code });
        break;
      case "table":
        blocks.push({ type: "p", text: [b.header, ...b.rows].map((row) => row.join(" · ")).join("\n") });
        break;
      case "hr":
        blocks.push({ type: "p", text: "---" });
        break;
      case "quote":
      case "paragraph": {
        const text = b.lines.join("\n");
        if (text) blocks.push({ type: "p", text });
        break;
      }
      default:
        break;
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

  // ── The three non-file stores. Each keeps its own "could not load" flag
  // rather than folding into the shared `error` banner: an unreadable facts
  // table must not read as "this agent has no facts", and it must not blank
  // the memory files sitting beside it either.
  const [facts, setFacts] = useState<MemoryFact[]>([]);
  const [factsError, setFactsError] = useState<string | null>(null);
  const [factsLoaded, setFactsLoaded] = useState(false);
  const [daily, setDaily] = useState<string>("");
  const [dailyError, setDailyError] = useState<string | null>(null);
  const [dailyLoaded, setDailyLoaded] = useState(false);
  const [privateNote, setPrivateNote] = useState<string>("");
  const [privateOriginal, setPrivateOriginal] = useState<string>("");
  const [privateError, setPrivateError] = useState<string | null>(null);
  const [privateLoaded, setPrivateLoaded] = useState(false);
  const [privateSaving, setPrivateSaving] = useState(false);

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
    // The three stores below are per-agent too, so they get the same
    // re-key treatment for the same reason: without it, agent A's facts
    // stay on screen under agent B's name, and a private-note Save would
    // PUT A's text at B's URL.
    setFacts([]);
    setFactsError(null);
    setFactsLoaded(false);
    setDaily("");
    setDailyError(null);
    setDailyLoaded(false);
    setPrivateNote("");
    setPrivateOriginal("");
    setPrivateError(null);
    setPrivateLoaded(false);
  }, [agentId]);

  // Facts load with the tree (the left list shows their COUNT, so it cannot
  // wait for a click); the day-log and the private note are pulled only when
  // opened — both can be large, and neither has anything to show in the list.
  const loadFacts = useCallback(async () => {
    try {
      const res = await fleetAuthorizedFetch(`${apiBase}/facts`, { credentials: "include" });
      const d = await res.json().catch(() => ({}));
      if (!res.ok || d?.ok === false) throw new Error(d?.error || `HTTP ${res.status}`);
      setFacts(Array.isArray(d?.facts) ? (d.facts as MemoryFact[]) : []);
      setFactsError(null);
    } catch (e) {
      setFactsError(e instanceof Error ? e.message : "Could not load facts.");
    } finally {
      setFactsLoaded(true);
    }
  }, [apiBase]);

  const loadDaily = useCallback(async () => {
    try {
      const res = await fleetAuthorizedFetch(`${apiBase}/daily`, { credentials: "include" });
      const d = await res.json().catch(() => ({}));
      if (!res.ok || d?.ok === false) throw new Error(d?.error || `HTTP ${res.status}`);
      setDaily(String(d?.content || ""));
      setDailyError(null);
    } catch (e) {
      setDailyError(e instanceof Error ? e.message : "Could not load recent activity.");
    } finally {
      setDailyLoaded(true);
    }
  }, [apiBase]);

  const loadPrivate = useCallback(async () => {
    try {
      const res = await fleetAuthorizedFetch(`${apiBase}/private-note`, { credentials: "include" });
      const d = await res.json().catch(() => ({}));
      if (!res.ok || d?.ok === false) throw new Error(d?.error || `HTTP ${res.status}`);
      const text = String(d?.note?.content || "");
      setPrivateNote(text);
      setPrivateOriginal(text);
      setPrivateError(null);
    } catch (e) {
      setPrivateError(e instanceof Error ? e.message : "Could not load your note.");
    } finally {
      setPrivateLoaded(true);
    }
  }, [apiBase]);

  async function deleteFact(key: string) {
    if (typeof window !== "undefined" && !window.confirm("Forget this? The agent will no longer know it.")) return;
    try {
      const res = await fleetAuthorizedFetch(`${apiBase}/facts?key=${encodeURIComponent(key)}`, {
        method: "DELETE",
        credentials: "include",
        headers: buildCookieAuthHeaders("DELETE", {}),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok || d?.ok === false) throw new Error(d?.error || `HTTP ${res.status}`);
      setFacts((prev) => prev.filter((f) => f.key !== key));
      setFactsError(null);
    } catch (e) {
      setFactsError(e instanceof Error ? e.message : "Could not forget that.");
    }
  }

  async function savePrivate() {
    setPrivateSaving(true);
    try {
      const res = await fleetAuthorizedFetch(`${apiBase}/private-note`, {
        method: "PUT",
        credentials: "include",
        headers: buildCookieAuthHeaders("PUT", { "Content-Type": "application/json" }),
        body: JSON.stringify({ content: privateNote }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok || d?.ok === false) throw new Error(d?.error || `HTTP ${res.status}`);
      setPrivateOriginal(privateNote);
      setPrivateError(null);
    } catch (e) {
      setPrivateError(e instanceof Error ? e.message : "Could not save your note.");
    } finally {
      setPrivateSaving(false);
    }
  }

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
  useEffect(() => { loadFacts(); }, [loadFacts]);

  const openView = useCallback((view: string) => {
    setSelected(view);
    setError(null);
    if (view === DAILY_VIEW && !dailyLoaded) loadDaily();
    if (view === PRIVATE_VIEW && !privateLoaded) loadPrivate();
  }, [dailyLoaded, privateLoaded, loadDaily, loadPrivate]);

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

  const isPseudo = selected !== null && PSEUDO_VIEWS.has(selected);

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

        {/* The three non-file stores, in the SAME picker. A second tab strip
            beside a list that is already the picker is the two-navigation-
            surfaces bug this codebase has shipped and reverted before. */}
        <div className="fleet-memory-file-list-title" style={{ marginTop: 14 }}>What it knows</div>
        <button
          type="button"
          className={`fleet-memory-file-item${selected === FACTS_VIEW ? " is-active" : ""}`}
          onClick={() => openView(FACTS_VIEW)}
        >
          <Sparkles size={13} strokeWidth={1.75} />
          <span className="fleet-memory-file-item-path">Facts</span>
          {/* Rendered only once the facts request has actually answered:
              a "0" printed while the fetch is still in flight is a claim
              this component cannot support yet, and reads as "it knows
              nothing" rather than "not loaded". */}
          {factsLoaded && !factsError && (
            <span className="fleet-memory-file-item-size">{facts.length}</span>
          )}
        </button>
        <button
          type="button"
          className={`fleet-memory-file-item${selected === DAILY_VIEW ? " is-active" : ""}`}
          onClick={() => openView(DAILY_VIEW)}
        >
          <CalendarClock size={13} strokeWidth={1.75} />
          <span className="fleet-memory-file-item-path">Recent activity</span>
        </button>
        <button
          type="button"
          className={`fleet-memory-file-item${selected === PRIVATE_VIEW ? " is-active" : ""}`}
          onClick={() => openView(PRIVATE_VIEW)}
        >
          <Lock size={13} strokeWidth={1.75} />
          <span className="fleet-memory-file-item-path">Your note</span>
        </button>
      </div>
      <div className="fleet-memory-editor">
        {selected === FACTS_VIEW ? (
          <>
            <div className="fleet-memory-editor-header">
              <span className="fleet-memory-editor-filename">Facts</span>
            </div>
            <div className="fleet-memory-preview-scroll">
              <p className="fleet-memory-file-list-hint" style={{ padding: 0, marginBottom: 12 }}>
                Short things this agent recorded for itself. Forgetting one removes it from
                everything the agent reads from then on.
              </p>
              {factsError ? (
                <div className="fleet-channel-expand-error">{factsError}</div>
              ) : !factsLoaded ? (
                <div aria-busy="true" aria-label="Loading facts">
                  {[88, 74, 92].map((w, i) => (
                    <div key={i} className="fleet-skeleton-bar" style={{ width: `${w}%`, height: 11, marginBottom: 10 }} />
                  ))}
                </div>
              ) : facts.length === 0 ? (
                <p className="fleet-page-state-body">Nothing recorded yet.</p>
              ) : (
                <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 8 }}>
                  {facts.map((f) => {
                    const tier = trustLabel(f.trust_tier, f.source_sender_name);
                    const when = formatFactTime(f.updated_at);
                    return (
                      <li
                        key={f.key}
                        style={{ display: "flex", alignItems: "flex-start", gap: 8, minWidth: 0 }}
                      >
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div className="fleet-memory-preview-p" style={{ margin: 0 }}>{f.content}</div>
                          {(tier || when) && (
                            <div className="fleet-memory-file-item-size" style={{ marginTop: 2 }}>
                              {[tier, when].filter(Boolean).join(" · ")}
                            </div>
                          )}
                        </div>
                        <button
                          type="button"
                          className="fleet-btn"
                          onClick={() => deleteFact(f.key)}
                          aria-label={`Forget: ${f.content.slice(0, 60)}`}
                          title="Forget this"
                        >
                          <Trash2 size={13} strokeWidth={1.75} />
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </>
        ) : selected === DAILY_VIEW ? (
          <>
            <div className="fleet-memory-editor-header">
              <span className="fleet-memory-editor-filename">Recent activity</span>
            </div>
            <div className="fleet-memory-preview-scroll">
              <p className="fleet-memory-file-list-hint" style={{ padding: 0, marginBottom: 12 }}>
                A running day-by-day note the agent keeps of its own turns — and reads back on
                its next one.
              </p>
              {dailyError ? (
                <div className="fleet-channel-expand-error">{dailyError}</div>
              ) : !dailyLoaded ? (
                <div aria-busy="true" aria-label="Loading recent activity">
                  {[92, 70, 84].map((w, i) => (
                    <div key={i} className="fleet-skeleton-bar" style={{ width: `${w}%`, height: 11, marginBottom: 10 }} />
                  ))}
                </div>
              ) : daily.trim() ? (
                <MemoryPreview content={daily} />
              ) : (
                <p className="fleet-page-state-body">Nothing in the last two weeks.</p>
              )}
            </div>
          </>
        ) : selected === PRIVATE_VIEW ? (
          <>
            <div className="fleet-memory-editor-header">
              <span className="fleet-memory-editor-filename">Your note</span>
              <div className="fleet-memory-editor-actions">
                <button
                  type="button"
                  className="fleet-btn fleet-btn--accent"
                  onClick={savePrivate}
                  disabled={privateSaving || !privateLoaded || privateNote === privateOriginal || !privateNote.trim()}
                >
                  {privateSaving ? <Loader2 size={13} style={{ animation: "spin 1s linear infinite" }} /> : "Save"}
                </button>
              </div>
            </div>
            <div className="fleet-memory-preview-scroll">
              <p className="fleet-memory-file-list-hint" style={{ padding: 0, marginBottom: 12 }}>
                How you personally want this agent to work with you. Yours alone — teammates
                have their own, and nobody can read yours.
              </p>
              {privateError && <div className="fleet-channel-expand-error">{privateError}</div>}
              {!privateLoaded ? (
                <div aria-busy="true" aria-label="Loading your note">
                  {[90, 76].map((w, i) => (
                    <div key={i} className="fleet-skeleton-bar" style={{ width: `${w}%`, height: 11, marginBottom: 10 }} />
                  ))}
                </div>
              ) : (
                <textarea
                  className="fleet-memory-editor-textarea"
                  value={privateNote}
                  onChange={(e) => setPrivateNote(e.currentTarget.value)}
                  placeholder="e.g. Answer briefly. I care about the trade-off, not the summary."
                  spellCheck={false}
                />
              )}
            </div>
          </>
        ) : selected && !isPseudo ? (
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

/**
 * The Profile sheet's "Files" list — "media and other files also going to
 * be there" (founder, 2026-08-19), sitting alongside Memory in the same
 * segment rather than a third tab, per his own instruction to put them
 * side by side.
 *
 * There is no per-agent file/attachment TABLE anywhere in this codebase —
 * verified before building this, not assumed (CLAUDE.md's own recurring
 * warning: "built, tested, and never wired"). `POST /api/sage-chat/
 * attachments` (sage_context_files_api.py) writes into a flat,
 * workspace-wide directory (`workspace_attachments_dir`) with no database
 * row and no list endpoint — a "Files" tab backed by that would either be a
 * dead control (nothing to list) or would need a new migration + list route,
 * out of scope for a UI relocation.
 *
 * What DOES exist, already wired, already fetched by this exact chat:
 * `agent_turns.attachments` is a real column (thread_service.record_user_turn's
 * own `attachments` parameter, distinct from `metadata`), and
 * `GET /threads/{thread_id}` already echoes each turn's `attachments` array
 * back (runtime_runs_api.normalize_thread_turn_record). Every file a person
 * has ever attached to a message in THIS agent's own thread is sitting in
 * the same response AgentChat.tsx already fetches to render history — this
 * module is the pure transform from that raw turn list to a flat,
 * deduplicated, newest-first file list. No new backend surface.
 */

export type AgentProfileFile = {
  fileId: string;
  filename: string;
  url: string;
  contentType: string;
  size: number;
  /** ISO timestamp of the turn that carried it, or null if the turn had none. */
  uploadedAt: string | null;
  /** Who attached it — the turn's own role. Anything else collapses to "other". */
  fromRole: "user" | "assistant" | "other";
};

type RawAttachment = {
  file_id?: unknown;
  filename?: unknown;
  content_type?: unknown;
  size?: unknown;
  url?: unknown;
};

type RawTurnLike = {
  role?: unknown;
  created_at?: unknown;
  attachments?: unknown;
};

function normalizeRole(role: unknown): "user" | "assistant" | "other" {
  const text = String(role || "").trim().toLowerCase();
  if (text === "user" || text === "assistant") return text;
  return "other";
}

function normalizeAttachment(raw: unknown): { fileId: string; filename: string; url: string; contentType: string; size: number } | null {
  if (!raw || typeof raw !== "object") return null;
  const a = raw as RawAttachment;
  const fileId = String(a.file_id || "").trim();
  const url = String(a.url || "").trim();
  // Both are required — a file with no id can't be deduplicated, and a file
  // with no url can't be opened, so either missing means this entry is not
  // usable and must not render as a dead link.
  if (!fileId || !url) return null;
  return {
    fileId,
    filename: String(a.filename || "").trim() || "Untitled file",
    url,
    contentType: String(a.content_type || "").trim(),
    size: typeof a.size === "number" && Number.isFinite(a.size) && a.size >= 0 ? a.size : 0,
  };
}

/**
 * Flattens every turn's `attachments` array into one deduplicated list,
 * newest first (a Telegram media grid reads newest-on-top; a fresh capture
 * or reference belongs near the composer, not buried under months of
 * history). A file_id repeated across turns (should not happen in practice
 * — each upload mints a fresh uuid — but nothing here assumes it can't) keeps
 * only its FIRST-encountered occurrence's metadata and sorts by that
 * occurrence's turn time, so a duplicate can never split into two rows.
 */
export function extractProfileFiles(turns: readonly RawTurnLike[]): AgentProfileFile[] {
  const byId = new Map<string, AgentProfileFile>();
  for (const turn of Array.isArray(turns) ? turns : []) {
    if (!turn || typeof turn !== "object") continue;
    const rawList = (turn as RawTurnLike).attachments;
    if (!Array.isArray(rawList) || rawList.length === 0) continue;
    const role = normalizeRole((turn as RawTurnLike).role);
    const createdAt = (turn as RawTurnLike).created_at;
    const uploadedAt = typeof createdAt === "string" && createdAt.trim() ? createdAt.trim() : null;
    for (const rawAttachment of rawList) {
      const normalized = normalizeAttachment(rawAttachment);
      if (!normalized || byId.has(normalized.fileId)) continue;
      byId.set(normalized.fileId, { ...normalized, uploadedAt, fromRole: role });
    }
  }
  return Array.from(byId.values()).sort((a, b) => {
    const at = a.uploadedAt ? Date.parse(a.uploadedAt) : 0;
    const bt = b.uploadedAt ? Date.parse(b.uploadedAt) : 0;
    return bt - at;
  });
}

/** True for the content types the composer's own upload `accept` list treats
 *  as an image (AgentChat.tsx's `accept=".txt,.text,.md,.markdown,.csv,.json,
 *  .png,.jpg,.jpeg,.gif,.webp,.heic,.heif"`) — used only to pick an icon, so
 *  an unrecognized type safely falls back to the generic file glyph rather
 *  than guessing. */
export function isImageAttachment(contentType: string): boolean {
  return /^image\//i.test(String(contentType || "").trim());
}

export function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "";
  if (bytes < 1000) return `${Math.round(bytes)} B`;
  if (bytes < 1_000_000) return `${(bytes / 1000).toFixed(1).replace(/\.0$/, "")} KB`;
  return `${(bytes / 1_000_000).toFixed(1).replace(/\.0$/, "")} MB`;
}

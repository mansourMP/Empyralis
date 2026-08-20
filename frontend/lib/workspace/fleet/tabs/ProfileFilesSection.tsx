"use client";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { useEffect, useState } from "react";
import { FileText, Image as ImageIcon, Loader2 } from "lucide-react";

import { extractProfileFiles, formatFileSize, isImageAttachment, type AgentProfileFile } from "../agent-profile-files";
import { timeAgo } from "../fleet-presentation";

/**
 * The Profile sheet's "Files" list — sits below Memory in the same segment
 * (agent-profile-shape.ts's own doc comment has the founder's quote and the
 * reasoning). Reads whichever real conversation the observation view
 * (tabs/WorkTab.tsx) currently has selected — `GET /api/threads/{threadId}`
 * — rather than any new endpoint; see agent-profile-files.ts's own header
 * for why no per-agent file store exists to build a richer surface on top
 * of. `threadId` is FleetAgentDetail's own state, kept in sync with
 * WorkTab's selection via its `onSelectThread` callback — this file has no
 * picker of its own (2026-08-20: there is no more owner-only "open chat" to
 * default to; see WorkTab's own module comment for why there is exactly
 * one session picker now).
 *
 * Independent fetch, not shared state with WorkTab — the same choice
 * MemoryTab already makes for its own `/memory/tree` read: this only needs
 * to run while the Profile sheet is open, not on every poll of the
 * observation view underneath it.
 */
export function ProfileFilesSection({
  workspaceId,
  threadId,
}: {
  workspaceId: string;
  threadId: string;
}) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [files, setFiles] = useState<AgentProfileFile[]>([]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const res = await fleetAuthorizedFetch(
          `/api/threads/${encodeURIComponent(threadId)}?workspace_id=${encodeURIComponent(workspaceId)}`,
          { credentials: "include" },
        );
        if (res.status === 404) {
          // No turns on this thread yet — not an error, just nothing
          // attached yet (same 404-means-empty convention AgentChat.tsx's
          // own loadThread already treats this endpoint with).
          if (!cancelled) { setFiles([]); setError(null); }
          return;
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const turns = Array.isArray(data?.turns) ? data.turns : [];
        if (!cancelled) setFiles(extractProfileFiles(turns));
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Could not load files.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [workspaceId, threadId]);

  if (loading) {
    return (
      <div className="agent-profile-files-loading" aria-busy="true" aria-label="Loading files">
        <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
        <span>Loading files…</span>
      </div>
    );
  }

  if (error) {
    return <p className="fleet-channel-expand-error" style={{ margin: 0 }}>{error}</p>;
  }

  if (files.length === 0) {
    return <p className="fleet-subtitle" style={{ margin: 0 }}>No files shared in this conversation yet.</p>;
  }

  return (
    <ul className="agent-profile-files-list">
      {files.map((file) => (
        <li key={file.fileId} className="agent-profile-file-row">
          <a
            href={`${file.url}${file.url.includes("?") ? "&" : "?"}workspace_id=${encodeURIComponent(workspaceId)}`}
            target="_blank"
            rel="noreferrer noopener"
            className="agent-profile-file-link"
          >
            <span className="agent-profile-file-icon">
              {isImageAttachment(file.contentType) ? <ImageIcon size={15} strokeWidth={1.75} /> : <FileText size={15} strokeWidth={1.75} />}
            </span>
            <span className="agent-profile-file-meta">
              <span className="agent-profile-file-name">{file.filename}</span>
              <span className="agent-profile-file-sub">
                {[formatFileSize(file.size), file.uploadedAt ? timeAgo(file.uploadedAt) : null].filter(Boolean).join(" · ")}
              </span>
            </span>
          </a>
        </li>
      ))}
    </ul>
  );
}

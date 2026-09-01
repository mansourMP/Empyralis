"use client";

/**
 * A project's documents, rendered as the GIT-SHAPED TREE the founder
 * approved: a document carries a `path` ("specs/api/auth.md") and a folder
 * is an INFERRED PREFIX of it — there are no folder objects anywhere, in
 * the database or in this file. The whole derivation lives in
 * document-tree.ts (pure, plain-tested); this file only draws what that
 * returns, so the ordering rule (directories before files, each
 * alphabetically) has exactly one implementation and a test can reach it
 * without a DOM.
 *
 * This replaced a flat two-column table (Document | Updated). The table was
 * correct for a project holding six documents and stopped being correct the
 * moment a path had a slash in it: every document sat at one level and the
 * structure an agent had deliberately written into the path was invisible.
 *
 * REAL LINKS — CLAUDE.md requires cmd-click/middle-click to work for
 * primary navigation, so every FILE row is a real Next <Link> to the
 * document's own URL. Folder rows are real <button>s: they change local
 * disclosure state and navigate nowhere, so an anchor there would be a lie
 * about what the control does (and would offer a middle-click that opens
 * nothing).
 *
 * NOT role="tree". A conforming ARIA tree owes the reader roving tabindex,
 * arrow-key traversal, Home/End and type-ahead; a half-built one announces
 * capabilities the keyboard does not actually have, which is worse for a
 * screen-reader user than the plain semantics used here — a labelled group
 * of buttons and links, every one of them in the natural tab order, with
 * aria-expanded saying whether a folder is open. If full tree keyboard
 * support is ever wanted, it is a deliberate piece of work, not a role
 * attribute.
 *
 * EXPANDED BY DEFAULT, and the state tracked is what is CLOSED rather than
 * what is open. Two reasons, both about the same failure: this list is a
 * table of contents, so opening it collapsed hides the only thing the page
 * exists to show and charges a click per folder to get it back; and holding
 * "closed" (instead of "open") means a folder that appears later — a new
 * document, a refresh, an agent filing something under a new prefix — is
 * visible the moment it exists rather than silently hidden by a default
 * nobody chose.
 */

import { useCallback, useState, type CSSProperties } from "react";
import Link from "next/link";
import { ChevronRight, FileText, Folder, FolderOpen, FolderGit2 } from "lucide-react";

import { CopyLinkButton } from "@/lib/ui/CopyLinkButton";
import type { FleetDocument } from "./documents-data";
import {
  buildDocumentTree,
  buildProjectDocumentTree,
  type DocumentTreeNode,
} from "./document-tree";
import { timeAgo } from "./fleet-presentation";
import "./document-tree.css";

/** Indentation is a CSS custom property rather than a hard-coded pixel
 *  padding so the depth step is one number in document-tree.css (and can
 *  shrink on a narrow viewport) instead of a magic multiplier in here. */
function depthStyle(depth: number): CSSProperties {
  return { "--doc-tree-depth": depth } as CSSProperties;
}

/** The title is not drawn beside the filename: on a workspace whose paths
 *  are derived from titles that would print two versions of the same words
 *  on every row. It rides as the row's tooltip instead, so the fuller name
 *  is one hover away when a filename and a title genuinely differ. */
function fileTooltip(doc: FleetDocument, name: string): string | undefined {
  const title = String(doc.title || "").trim();
  if (!title || title.toLowerCase() === name.toLowerCase()) return undefined;
  return title;
}

function TreeNodes({
  nodes,
  depth,
  keyPrefix,
  closed,
  onToggle,
  hrefFor,
}: {
  nodes: DocumentTreeNode[];
  depth: number;
  /** Namespaces the disclosure key. Two projects can each hold a "specs"
   *  folder, and in the cross-project view they must open and close
   *  independently — a bare folder path would collapse both at once. */
  keyPrefix: string;
  closed: Set<string>;
  onToggle: (key: string) => void;
  hrefFor: (document: FleetDocument) => string;
}) {
  return (
    <>
      {nodes.map((node) => {
        if (node.kind === "file") {
          const tooltip = fileTooltip(node.document, node.name);
          const href = hrefFor(node.document);
          return (
            <Link
              key={node.document.id}
              href={href}
              className="fleet-doc-tree-row fleet-doc-tree-row--file"
              style={depthStyle(depth)}
              title={tooltip}
            >
              <span className="fleet-doc-tree-spacer" aria-hidden="true" />
              <FileText size={14} strokeWidth={1.75} className="fleet-doc-tree-icon" aria-hidden="true" />
              <span className="fleet-doc-tree-name">{node.name}</span>
              <span className="fleet-doc-tree-meta">{timeAgo(node.document.updated_at)}</span>
              {/* Copy link — the one per-row hover affordance this tree had
                  none of before (folders aren't real objects, so only a FILE
                  row gets one). Same CopyLinkButton every other row/detail
                  surface shares; its own click handler stops this row's real
                  Next <Link> from also navigating, the identical nested-
                  interactive pattern the project list row already uses
                  (projects/page.tsx) inside this exact component type. */}
              <CopyLinkButton
                path={href}
                label={node.name}
                className="fleet-icon-btn fleet-doc-tree-copy"
                iconSize={13}
              />
            </Link>
          );
        }

        const key = `${keyPrefix}${node.path}`;
        const open = !closed.has(key);
        return (
          <div key={key}>
            <button
              type="button"
              className="fleet-doc-tree-row fleet-doc-tree-row--folder"
              style={depthStyle(depth)}
              aria-expanded={open}
              onClick={() => onToggle(key)}
            >
              <ChevronRight size={14} strokeWidth={2} className="fleet-doc-tree-chevron" aria-hidden="true" />
              {open ? (
                <FolderOpen size={14} strokeWidth={1.75} className="fleet-doc-tree-icon" aria-hidden="true" />
              ) : (
                <Folder size={14} strokeWidth={1.75} className="fleet-doc-tree-icon" aria-hidden="true" />
              )}
              <span className="fleet-doc-tree-name">{node.name}</span>
              {/* Only while CLOSED: an open folder already shows what it
                  holds, so a count beside it is the same fact twice. */}
              {open ? null : <span className="fleet-doc-tree-count">{node.fileCount}</span>}
            </button>
            {open ? (
              <div role="group" aria-label={node.name}>
                <TreeNodes
                  nodes={node.children}
                  depth={depth + 1}
                  keyPrefix={keyPrefix}
                  closed={closed}
                  onToggle={onToggle}
                  hrefFor={hrefFor}
                />
              </div>
            ) : null}
          </div>
        );
      })}
    </>
  );
}

/** Tracks what is CLOSED, never what is open — see this file's header. */
function useDisclosure() {
  const [closed, setClosed] = useState<Set<string>>(() => new Set());
  const onToggle = useCallback((key: string) => {
    setClosed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);
  return { closed, onToggle };
}

/**
 * ONE PROJECT's tree. Same props the flat table had, so the project page
 * needed no change: `hrefFor` takes a document id because inside a project
 * every document's URL is built from the same project base.
 */
export function DocumentsList({
  documents,
  hrefFor,
}: {
  documents: FleetDocument[];
  hrefFor: (documentId: string) => string;
}) {
  const { closed, onToggle } = useDisclosure();
  const nodes = buildDocumentTree(documents);
  const href = useCallback((doc: FleetDocument) => hrefFor(doc.id), [hrefFor]);

  return (
    <div className="fleet-doc-tree">
      <TreeNodes
        nodes={nodes}
        depth={0}
        keyPrefix=""
        closed={closed}
        onToggle={onToggle}
        hrefFor={href}
      />
    </div>
  );
}

/**
 * THE WORKSPACE's tree (/w/{id}/context): PROJECT at the top level, that
 * project's own inferred tree beneath it — org / repo / path, the mapping
 * the founder approved. Same rows, same disclosure, same component beneath
 * the project heading; only the grouping is different, and it lives in
 * document-tree.ts beside the tree rule it belongs with.
 *
 * `hrefFor` takes the whole document here rather than an id: a document's
 * URL is inside ITS project, and this view spans several.
 */
export function WorkspaceDocumentsTree({
  documents,
  projects,
  hrefFor,
}: {
  documents: FleetDocument[];
  projects: { id: string; name: string }[];
  hrefFor: (document: FleetDocument) => string;
}) {
  const { closed, onToggle } = useDisclosure();
  const groups = buildProjectDocumentTree(documents, projects);

  return (
    <div className="fleet-doc-tree">
      {groups.map((group) => {
        const key = `project:${group.projectId}`;
        const open = !closed.has(key);
        return (
          <section key={key} className="fleet-doc-tree-project">
            {/* A real h2 — the page's h1 is the breadcrumb's current crumb
                (Breadcrumbs.tsx), so a project's name here is its genuine
                child heading, not a styled div. The disclosure control sits
                INSIDE the heading so the heading text is what a screen
                reader announces and what its outline carries. */}
            <h2 className="fleet-doc-tree-project-head">
              <button
                type="button"
                className="fleet-doc-tree-row fleet-doc-tree-row--project"
                aria-expanded={open}
                onClick={() => onToggle(key)}
              >
                <ChevronRight size={14} strokeWidth={2} className="fleet-doc-tree-chevron" aria-hidden="true" />
                <FolderGit2 size={15} strokeWidth={1.75} className="fleet-doc-tree-icon" aria-hidden="true" />
                <span className="fleet-doc-tree-name">{group.name}</span>
                {open ? null : <span className="fleet-doc-tree-count">{group.fileCount}</span>}
              </button>
            </h2>
            {open ? (
              <div role="group" aria-label={group.name}>
                <TreeNodes
                  nodes={group.children}
                  depth={1}
                  keyPrefix={`${key}/`}
                  closed={closed}
                  onToggle={onToggle}
                  hrefFor={hrefFor}
                />
              </div>
            ) : null}
          </section>
        );
      })}
    </div>
  );
}

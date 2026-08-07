"use client";

/**
 * A project's Documents list — Title + Updated, the same two-column reading
 * this project needs and nothing else (no status/assignee/due columns to
 * borrow from Tasks). Reuses TasksList.tsx's own grid idiom wholesale
 * (.fleet-tasks-list / -header / .fleet-task-row, the `--fleet-list-grid`
 * custom-property override for a column count TasksList's own CSS wasn't
 * built for) rather than inventing a second list shape.
 *
 * REAL LINKS, unlike TaskRow (which is an onClick div — see that file):
 * CLAUDE.md requires cmd-click/middle-click to work for primary navigation,
 * so each row is a real Next <Link> to the document's own URL
 * (`${projectHref}/documents/{id}`, the same pattern taskHref uses one
 * segment over).
 */

import type { CSSProperties } from "react";
import Link from "next/link";

import type { FleetDocument } from "./documents-data";
import { timeAgo } from "./fleet-presentation";

const GRID = "minmax(260px, 1fr) 96px";

export function DocumentsList({
  documents,
  hrefFor,
}: {
  documents: FleetDocument[];
  hrefFor: (documentId: string) => string;
}) {
  return (
    <div className="fleet-tasks-list" style={{ "--fleet-list-grid": GRID } as CSSProperties}>
      <div className="fleet-tasks-list-header" role="row">
        <span>Document</span>
        <span className="is-right">Updated</span>
      </div>
      {documents.map((doc) => (
        <Link key={doc.id} href={hrefFor(doc.id)} className="fleet-task-row" role="row">
          <span className="fleet-agent-cell-agent-text fleet-task-cell-title">
            <span className="fleet-task-cell-titleline">
              <span className="fleet-agent-name">{doc.title || "Untitled document"}</span>
            </span>
          </span>
          <span className="fleet-agent-cell-right fleet-cell-secondary">
            {timeAgo(doc.updated_at)}
          </span>

          {/* Mobile replacement — same collapse TaskRow's own mobile block
              uses, minus the properties this list doesn't have. */}
          <div className="fleet-agent-row-mobile">
            <div className="fleet-agent-row-mobile-line1">
              <span className="fleet-agent-row-mobile-name">{doc.title || "Untitled document"}</span>
            </div>
            <div className="fleet-agent-row-mobile-line2">Updated {timeAgo(doc.updated_at)}</div>
          </div>
        </Link>
      ))}
    </div>
  );
}

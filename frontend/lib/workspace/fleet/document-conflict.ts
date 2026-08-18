/**
 * What the person is shown, and offered, when a document changed underneath
 * them while they were typing.
 *
 * THE BUG THIS BELONGS TO. The document page snapshots title+body into a
 * draft on open and never refetches (documents-data.ts's own header used to
 * assert documents are "not mutated out from under the reader by an agent",
 * which stopped being true the day document__edit shipped). Autosave then
 * PATCHes the WHOLE body from that snapshot every ~900ms. Any agent edit
 * landing in between was silently overwritten, and the revision history
 * recorded a clean row saying the HUMAN wrote the reversion.
 *
 * The backend now refuses that write (project_documents_repository's
 * `expected_sha256` compare-and-swap -> HTTP 409). This module owns the
 * other half, which is the half that decides whether the fix is real:
 *
 *   A fix whose failure mode is "the human loses their paragraph instead of
 *   the agent" is the same data loss pointed the other way.
 *
 * So the rule here is that NOBODY's text is thrown away by the product. On a
 * conflict the person's draft stays exactly as typed, autosave stops (a
 * doomed save must not re-fire on every keystroke), and the two ways out are
 * both EXPLICIT, both labelled with their real consequence, and neither is
 * a default:
 *
 *   keep-mine    rebase onto the current state and save the person's
 *                version. The other version is NOT destroyed -- it is a
 *                revision, visible in the document's own history, which is
 *                what makes this a safe default-less choice rather than a
 *                second overwrite.
 *   take-theirs  replace the draft with the server's version. This DOES
 *                discard what the person typed (it was never saved, so no
 *                revision holds it) -- which is exactly why it is worded as
 *                discarding and never offered as the quiet option.
 *
 * DELIBERATELY NOT BUILT, and flagged rather than guessed: an automatic
 * three-way merge (base / mine / theirs). The base text is recoverable --
 * the client holds the draft's origin and the server holds every revision --
 * so it is buildable, but a merge that silently picks wrong on overlapping
 * edits reintroduces exactly this bug in a form nobody can see. Showing both
 * versions and letting a person decide is the honest half; auto-merge is a
 * product decision, not a drive-by one.
 *
 * Pure data + pure functions in their own module for the same reason
 * channel-doors.ts and agent-count-shape.ts are: document-conflict.test.ts
 * imports THESE functions, so the expected behaviour and the actual
 * behaviour come from different places (a component-embedded rule would only
 * ever be re-asserted by a copy of itself).
 */

export type DocumentConflictSide = {
  title: string;
  body: string;
};

export type DocumentConflictAction = {
  key: "keep-mine" | "take-theirs";
  label: string;
  /** What this button actually does to the two versions. Stated on the
   *  control itself -- a choice between two versions of a person's own
   *  writing is not one to explain in a paragraph above the buttons. */
  consequence: string;
  /** Exactly one action carries the accent (craft doctrine: one accent
   *  colour, spent on the single primary action in a view). "Keep mine" is
   *  it, because it is the only one of the two that loses nothing. */
  accent: boolean;
};

export type DocumentConflictPlan = {
  /** False when the incoming version is byte-identical to the draft -- the
   *  other writer changed nothing the person can see, so there is nothing to
   *  resolve and nothing to interrupt them about. The caller adopts the new
   *  precondition token and carries on. */
  needsResolution: boolean;
  headline: string;
  /** The reassurance, and it must be TRUE: the draft is untouched and
   *  unsaved. */
  reassurance: string;
  actions: DocumentConflictAction[];
};

/** Who changed it, in the words a person uses. `null` when the actor cannot
 *  be resolved -- and that renders as "Someone else", never as a guess and
 *  never as an id. Same posture DocumentHistory already takes for a failed
 *  identity lookup: an unresolved actor must not read as a resolved one. */
export function conflictActorLabel(name: string | null | undefined): string {
  const trimmed = String(name || "").trim();
  return trimmed || "Someone else";
}

export function planDocumentConflict(
  mine: DocumentConflictSide,
  theirs: DocumentConflictSide,
  actorName?: string | null,
): DocumentConflictPlan {
  const identical = mine.title === theirs.title && mine.body === theirs.body;
  if (identical) {
    return {
      needsResolution: false,
      headline: "",
      reassurance: "",
      actions: [],
    };
  }
  const actor = conflictActorLabel(actorName);
  return {
    needsResolution: true,
    headline: `${actor} changed this document while you were editing.`,
    // Three facts kept apart on purpose (CLAUDE.md's standing law): this is
    // not "couldn't save" (a failure to retry) and not "saved" (a lie) --
    // it is a refusal, with the person's work intact and a decision to make.
    reassurance: "Your changes are still here and have not been saved yet.",
    actions: [
      {
        key: "keep-mine",
        label: "Keep my version",
        consequence: `Saves your version. ${actor}'s stays in this document's history.`,
        accent: true,
      },
      {
        key: "take-theirs",
        label: "Use theirs instead",
        consequence: "Discards what you typed here. It was never saved, so it cannot be recovered.",
        accent: false,
      },
    ],
  };
}

export type DiffLine = {
  kind: "context" | "add" | "remove";
  text: string;
};

/**
 * A line diff between the person's draft and the incoming version, so the
 * choice above is made while LOOKING at both rather than in the dark.
 *
 * `-` is the person's own draft and `+` is the incoming version, matching
 * the direction the buttons read in ("keep mine" keeps the `-` side). This
 * is deliberately NOT the server's stored revision diff: that one compares
 * two SAVED states, and the thing the person needs to see is their own
 * unsaved text against what is on the server now -- a comparison only the
 * client can make, because only the client has the draft.
 *
 * Plain LCS over lines, no hunk headers: the surface renders inside a
 * conflict banner, not a code review, and the existing
 * `.fleet-doc-diff-line--add/--remove` styling from DocumentHistory carries
 * it. Bounded by `maxLines` so a whole-document rewrite renders a readable
 * excerpt rather than a thousand rows inside a banner.
 */
export function diffDocumentBodies(
  mineBody: string,
  theirsBody: string,
  maxLines = 40,
): { lines: DiffLine[]; truncated: boolean } {
  const a = String(mineBody || "").split("\n");
  const b = String(theirsBody || "").split("\n");

  // LCS table. Bodies here are markdown notes, not machine output, so the
  // O(n*m) table is fine at realistic sizes; the guard below keeps a
  // pathological paste from freezing the tab.
  const CELL_BUDGET = 4_000_000;
  if (a.length * b.length > CELL_BUDGET) {
    return {
      lines: [
        { kind: "remove", text: `${a.length} lines in your version` },
        { kind: "add", text: `${b.length} lines in theirs` },
      ],
      truncated: true,
    };
  }

  const lcs: number[][] = Array.from({ length: a.length + 1 }, () =>
    new Array<number>(b.length + 1).fill(0),
  );
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }

  const all: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      all.push({ kind: "context", text: a[i] });
      i += 1;
      j += 1;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      all.push({ kind: "remove", text: a[i] });
      i += 1;
    } else {
      all.push({ kind: "add", text: b[j] });
      j += 1;
    }
  }
  while (i < a.length) {
    all.push({ kind: "remove", text: a[i] });
    i += 1;
  }
  while (j < b.length) {
    all.push({ kind: "add", text: b[j] });
    j += 1;
  }

  // Unchanged lines are the bulk of any real document and carry no
  // information here -- collapse runs of them so the changes are what the
  // eye lands on, keeping one line of context either side.
  const condensed: DiffLine[] = [];
  for (let k = 0; k < all.length; k += 1) {
    const line = all[k];
    if (line.kind !== "context") {
      condensed.push(line);
      continue;
    }
    const prevChanged = k > 0 && all[k - 1].kind !== "context";
    const nextChanged = k + 1 < all.length && all[k + 1].kind !== "context";
    if (prevChanged || nextChanged) condensed.push(line);
  }

  const truncated = condensed.length > maxLines;
  return { lines: truncated ? condensed.slice(0, maxLines) : condensed, truncated };
}

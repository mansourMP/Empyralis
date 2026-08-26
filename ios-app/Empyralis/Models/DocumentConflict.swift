import Foundation

/// What the person is shown, and offered, when a document changed underneath
/// them while they were editing — a direct port of the web's
/// `document-conflict.ts`, kept behaviourally identical on purpose (same
/// headline shape, same two actions, same diff algorithm) so the product
/// reads as one thing across platforms even though this screen's chrome is
/// bespoke to a phone.
///
/// THE BUG THIS BELONGS TO, restated from the web's own header because it is
/// the reason every choice below is shaped the way it is: the backend now
/// refuses a stale write outright (`project_documents_repository`'s
/// `expected_sha256` compare-and-swap → HTTP 409, see
/// DocumentWriteAPI.swift). That closes HALF the bug. The other half is this
/// module: a fix whose failure mode is "the person loses their paragraph
/// instead of the agent" is the identical data loss pointed the other way.
///
/// So the rule is that NOBODY'S text is thrown away by this product. On a
/// conflict the draft stays exactly as typed, further save attempts stop
/// (DocumentEditSheet's own `.conflict` state blocks the Save button), and
/// the two ways out are both EXPLICIT, both labelled with their real
/// consequence, and neither is a default the person can arrive at by
/// accident.
///
/// DELIBERATELY NOT BUILT, same as the web: an automatic three-way merge.
/// The base text is recoverable (the draft's own origin, held in
/// DocumentEditSheet, plus every revision on the server), so it is
/// buildable — but a merge that silently picks wrong on overlapping edits
/// reintroduces exactly this bug in a form nobody can see. Showing both
/// versions and letting a person decide is the honest half.
enum DocumentConflict {

    struct Side: Equatable {
        let title: String
        let body: String
    }

    struct Action: Equatable, Identifiable {
        enum Key: String { case keepMine = "keep-mine", takeTheirs = "take-theirs" }
        let key: Key
        let label: String
        /// What this action actually does to the two versions, stated on the
        /// control itself — a choice between two versions of a person's own
        /// writing is not one to explain in a paragraph above the buttons.
        let consequence: String
        /// Exactly one action carries the accent (craft doctrine: one accent
        /// colour, spent on the single primary action in a view). "Keep
        /// mine" is it, because it is the only one of the two that loses
        /// nothing.
        let accent: Bool
        var id: String { key.rawValue }
    }

    struct Plan: Equatable {
        /// False when the incoming version is byte-identical to the draft —
        /// the other writer changed nothing the person can see, so there is
        /// nothing to resolve and nothing to interrupt them about. The
        /// caller adopts the new precondition token and carries on.
        let needsResolution: Bool
        let headline: String
        /// The reassurance, and it must be TRUE: the draft is untouched and
        /// unsaved.
        let reassurance: String
        let actions: [Action]

        static let none = Plan(needsResolution: false, headline: "", reassurance: "", actions: [])
    }

    /// Who changed it, in the words a person uses. `nil`/empty resolves to
    /// "Someone else" — never a guess, never a raw id. Same posture
    /// ActorResolver already takes for an unresolved identity elsewhere in
    /// this app.
    static func actorLabel(_ name: String?) -> String {
        let trimmed = (name ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "Someone else" : trimmed
    }

    static func plan(mine: Side, theirs: Side, actorName: String? = nil) -> Plan {
        guard mine.title != theirs.title || mine.body != theirs.body else {
            return .none
        }
        let actor = actorLabel(actorName)
        return Plan(
            needsResolution: true,
            headline: "\(actor) changed this document while you were editing.",
            // Three facts kept apart on purpose (CLAUDE.md's standing law):
            // this is not "couldn't save" (a failure to retry) and not
            // "saved" (a lie) — it is a refusal, with the person's work
            // intact and a decision to make.
            reassurance: "Your changes are still here and have not been saved yet.",
            actions: [
                Action(
                    key: .keepMine,
                    label: "Keep my version",
                    consequence: "Saves your version. \(actor)'s stays in this document's history.",
                    accent: true
                ),
                Action(
                    key: .takeTheirs,
                    label: "Use theirs instead",
                    consequence: "Discards what you typed here. It was never saved, so it cannot be recovered.",
                    accent: false
                ),
            ]
        )
    }

    // MARK: - Diff

    enum DiffLineKind { case context, add, remove }

    struct DiffLine: Equatable {
        let kind: DiffLineKind
        let text: String
    }

    struct DiffResult {
        let lines: [DiffLine]
        let truncated: Bool
    }

    /// A line diff between the person's draft and the incoming version, so
    /// the choice above is made while LOOKING at both rather than in the
    /// dark.
    ///
    /// `-` (remove) is the person's OWN draft and `+` (add) is the incoming
    /// version — matching the direction the buttons read in ("keep mine"
    /// keeps the removed side). This is deliberately NOT the server's stored
    /// revision diff: that one compares two SAVED states, and the thing the
    /// person needs to see is their own UNSAVED text against what is on the
    /// server now — a comparison only the client can make, because only the
    /// client holds the draft.
    ///
    /// Plain LCS over lines, no hunk headers — a phone screen has even less
    /// room than the web's conflict banner, so this renders inside a plain
    /// scrollable list rather than a code-review-style hunk view. Bounded by
    /// `maxLines` so a whole-document rewrite renders a readable excerpt
    /// rather than hundreds of rows inside a sheet, same as the web.
    static func diffBodies(_ mineBody: String, _ theirsBody: String, maxLines: Int = 40) -> DiffResult {
        let a = mineBody.components(separatedBy: "\n")
        let b = theirsBody.components(separatedBy: "\n")

        // LCS table. Bodies here are markdown notes, not machine output, so
        // the O(n*m) table is fine at realistic sizes; the guard below keeps
        // a pathological paste from freezing the UI — same cell budget the
        // web's own diffDocumentBodies uses, so the two degrade at the same
        // point.
        let cellBudget = 4_000_000
        guard a.count * b.count <= cellBudget else {
            return DiffResult(
                lines: [
                    DiffLine(kind: .remove, text: "\(a.count) lines in your version"),
                    DiffLine(kind: .add, text: "\(b.count) lines in theirs"),
                ],
                truncated: true
            )
        }

        // Flat array, not [[Int]] — a nested Swift array of ~2000x2000 Ints
        // carries far more bridging overhead than JS's own flat typed
        // arrays; indexing `i * (bCount + 1) + j` into one contiguous buffer
        // keeps this fast enough to run on the main actor without a visible
        // hitch when a real conflict fires.
        let aCount = a.count
        let bCount = b.count
        let rowStride = bCount + 1
        var lcs = [Int](repeating: 0, count: (aCount + 1) * rowStride)
        // Bottom-up: i from aCount-1 down to 0, j from bCount-1 down to 0 —
        // mirrors the web's own fill order exactly (each cell depends only on
        // cells with a larger i or j, all already computed).
        for i in stride(from: aCount - 1, through: 0, by: -1) {
            for j in stride(from: bCount - 1, through: 0, by: -1) {
                let here = i * rowStride + j
                if a[i] == b[j] {
                    lcs[here] = lcs[(i + 1) * rowStride + (j + 1)] + 1
                } else {
                    lcs[here] = max(lcs[(i + 1) * rowStride + j], lcs[i * rowStride + (j + 1)])
                }
            }
        }

        var all: [DiffLine] = []
        var i = 0
        var j = 0
        while i < aCount && j < bCount {
            if a[i] == b[j] {
                all.append(DiffLine(kind: .context, text: a[i]))
                i += 1
                j += 1
            } else if lcs[(i + 1) * rowStride + j] >= lcs[i * rowStride + (j + 1)] {
                all.append(DiffLine(kind: .remove, text: a[i]))
                i += 1
            } else {
                all.append(DiffLine(kind: .add, text: b[j]))
                j += 1
            }
        }
        while i < aCount { all.append(DiffLine(kind: .remove, text: a[i])); i += 1 }
        while j < bCount { all.append(DiffLine(kind: .add, text: b[j])); j += 1 }

        // Unchanged lines are the bulk of any real document and carry no
        // information here — collapse runs of them so the changes are what
        // the eye lands on, keeping one line of context either side.
        var condensed: [DiffLine] = []
        for k in 0..<all.count {
            let line = all[k]
            if line.kind != .context {
                condensed.append(line)
                continue
            }
            let prevChanged = k > 0 && all[k - 1].kind != .context
            let nextChanged = k + 1 < all.count && all[k + 1].kind != .context
            if prevChanged || nextChanged {
                condensed.append(line)
            }
        }

        let truncated = condensed.count > maxLines
        return DiffResult(lines: truncated ? Array(condensed.prefix(maxLines)) : condensed, truncated: truncated)
    }
}

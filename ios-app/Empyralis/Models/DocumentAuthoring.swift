import Foundation

/// Pure decision logic for document editing — same "pull the decision out and
/// test it directly" pattern `TaskAuthoring.swift` already uses for tasks.
enum DocumentAuthoring {

    /// Whether the signed-in person may reasonably expect a document write to
    /// succeed in THIS workspace — used only to decide whether to RENDER the
    /// edit affordance (CLAUDE.md: "no dead controls"), never to gate the
    /// actual write, which `fleet_patch_document` still enforces server-side
    /// regardless.
    ///
    /// THIS IS COARSER THAN THE WEB'S `useCanWriteProject`, AND DELIBERATELY
    /// SO — worth stating plainly rather than quietly passing as full parity.
    /// The web's real gate is: a workspace OWNER always passes; anyone else
    /// needs a WORKSPACE role of `member` or higher AND an explicit
    /// `project_memberships` row for THIS document's own project
    /// (`project-members-data.ts`'s `deriveCanWriteProject`, mirroring
    /// `auth.enforce_project_access`'s real policy). The per-project half
    /// needs a second fetch (`GET .../fleet/projects/{id}/members`) this app
    /// does not make anywhere today, and `WorkspaceStore` does not carry
    /// project membership at all.
    ///
    /// So this checks only the WORKSPACE role, which `WorkspaceStore.members`
    /// already carries at zero extra cost (no new fetch, no new network
    /// round trip): an `owner` passes (matches the web exactly — an owner's
    /// answer never depends on project membership either, per
    /// `enforce_project_access`'s own RBAC short-circuit), a `viewer` is
    /// refused (matches the web exactly — a sub-`member` role always reads
    /// `false` on the web too, before it ever looks at project membership),
    /// and a `member` reads `true` here where the web might still say `false`
    /// for a project they were never added to. That gap is real: a `member`
    /// who lacks this document's project membership will see the edit
    /// affordance, tap it, and be told by the server it refused (a plain,
    /// readable `enforce_project_access` 403) rather than never seeing the
    /// control at all. That is a live, readable failure — never a silent
    /// no-op — so it does not violate "no dead controls" in the strict sense
    /// (the control is not GUARANTEED dead), but it is not the same
    /// precision the web has. Closing that gap fully needs a project-members
    /// fetch this pass does not add.
    ///
    /// An unresolved own role (no row for `ownUserId` in `members` at all —
    /// e.g. the members fetch itself failed and nothing is cached) reads as
    /// `false`, mirroring the web's own `canWrite === null` → renders as
    /// unwritable contract exactly: "if it cannot tell, do not guess towards
    /// showing a control that might not work."
    static func canWrite(members: [WorkspaceMember], ownUserId: String?) -> Bool {
        guard let ownUserId, !ownUserId.isEmpty else { return false }
        guard let role = members.first(where: { $0.userId == ownUserId })?.role else { return false }
        return role == "owner" || role == "member"
    }

    /// The title actually sent on save. Mirrors DocumentDetailView.tsx's own
    /// `runSave` exactly: `draftRef.current.title.trim() || "Untitled
    /// document"` — never an empty string, because the backend's own
    /// `title = COALESCE(NULLIF($4, ''), title)` silently IGNORES an empty
    /// title rather than clearing it (project_documents_repository.
    /// update_document). Sending "" and calling it saved would be a lie: the
    /// title on the server would not have changed. Unlike a task's title
    /// (TaskAuthoring.canSaveEdit(requiresNonEmpty: true), which REFUSES to
    /// save a blank title at all), a document substitutes a real fallback
    /// value instead — matching the web, which never disables Save merely
    /// because the title field is blank.
    static func resolvedTitle(_ draft: String) -> String {
        let trimmed = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "Untitled document" : trimmed
    }

    /// Whether there is a REAL, savable difference between the draft and the
    /// last state this screen confirmed with the server — the single
    /// predicate that drives four things at once: the Save button's enabled
    /// state, whether Cancel needs a discard confirmation, whether
    /// interactive sheet dismissal is blocked, and whether the local draft
    /// (see DocumentEditSheet's own header) is worth persisting to disk.
    /// One tested function rather than four independently-reasoned call
    /// sites is deliberate — CLAUDE.md's own "a guard called once inside a
    /// large surface is a guard the next branch will skip" applies just as
    /// much to a predicate reimplemented four times as to a safety guard.
    ///
    /// Title is compared AFTER `resolvedTitle` (trimmed + defaulted) exactly
    /// like DocumentDetailView.tsx's own `runSave` compares
    /// `title === lastSavedRef.current.title` against an already-resolved
    /// value — a person adding then removing trailing whitespace in the
    /// title field must not read as a change. BODY IS NEVER TRIMMED: a
    /// document's body is markdown, where trailing whitespace inside a code
    /// fence or a deliberate blank line at the end can be real content, and
    /// `update_document`'s own SQL treats `body` as an unconditional
    /// `COALESCE($5, body)` — an omitted field means untouched, but a value
    /// once sent is sent VERBATIM, so this predicate must judge it the same
    /// way the server will.
    static func canSave(draftTitle: String, draftBody: String, lastSavedTitle: String, lastSavedBody: String) -> Bool {
        resolvedTitle(draftTitle) != lastSavedTitle || draftBody != lastSavedBody
    }
}

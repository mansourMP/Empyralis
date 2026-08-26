import Foundation

/// Pure decision logic for the four task-authoring surfaces (create a task,
/// create a sub-task, edit a title, edit a description) — extracted rather
/// than left as private computed properties buried inside the view structs
/// that use them, the same "pull the decision out and test it directly"
/// pattern this screen's own neighbours already use (InboxNeedsYou.swift,
/// MyWork.swift).
enum TaskAuthoring {

    /// Whether a text-edit sheet's Save action should be enabled. Requires
    /// a REAL change — re-saving the exact value that is already there
    /// would be a no-op PATCH the backend silently absorbs (title) or a
    /// spurious write with nothing to show for it (description).
    ///
    /// `requiresNonEmpty` is what lets one function serve both fields:
    /// TITLE must never go blank — the backend's own
    /// `title = COALESCE(NULLIF($4, ''), title)` silently IGNORES an empty
    /// string rather than clearing it, so refusing to enable Save here is
    /// the honest signal, not a duplicate of a server-side rule (a "saved"
    /// that silently changed nothing would be a lie). DESCRIPTION has no
    /// such floor — an empty save is how a description is deliberately
    /// CLEARED, and `description = COALESCE($5, description)` applies it.
    static func canSaveEdit(draft: String, initial: String, requiresNonEmpty: Bool) -> Bool {
        let trimmedDraft = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedInitial = initial.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmedDraft != trimmedInitial else { return false }
        return !requiresNonEmpty || !trimmedDraft.isEmpty
    }

    /// Whether "Create task" should be enabled. Title is the only required
    /// field on this screen — see NewTaskSheet's own header comment for why
    /// status/priority/assignee/labels/due all stay off it.
    static func canCreateTask(title: String, isCreating: Bool) -> Bool {
        !isCreating && !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// The Sub-tasks badge's total. The server's own rollup
    /// (`task.subtask_count`) can briefly lag a sub-task THIS SCREEN just
    /// created — appended locally with no round trip yet to bump the
    /// parent's count — so the LARGER of the two numbers is shown. That can
    /// only ever be a moment behind a real refresh; it can never disagree
    /// with a row already visible on screen. The reverse case (the rollup
    /// already knows about more than have synced locally) is the existing,
    /// unchanged behaviour — this is the same rule covering both.
    static func subtaskTotal(rollup: Int, synced: Int) -> Int {
        max(rollup, synced)
    }
}

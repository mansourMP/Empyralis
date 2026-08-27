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

    /// Whether the signed-in person may reasonably expect a task WRITE to
    /// succeed in this workspace — used only to decide whether to RENDER a
    /// write control (CLAUDE.md: "no dead controls"), never to gate the
    /// write itself, which every one of these routes still enforces
    /// server-side regardless: `fleet_create_task` / `fleet_patch_task` /
    /// `fleet_assign_task` / `fleet_attach_task_label` /
    /// `fleet_detach_task_label` / `fleet_comment_task` (routes_fleet.py)
    /// all call `enforce_workspace_access(..., minimum_role="member")` —
    /// confirmed for every one of them, not assumed from the one comment
    /// on WorkspaceStore.setTaskStatus that first flagged this.
    ///
    /// Same rule and same shape as `DocumentAuthoring.canWrite` — kept as
    /// its own copy rather than a shared call, matching this file's own
    /// pattern of one small pure enum per surface (TaskAuthoring /
    /// DocumentAuthoring / MyWork / InboxNeedsYou) rather than reaching
    /// across surfaces for a generic helper — but the REASONING is
    /// identical and worth restating rather than silently diverging:
    /// `owner` always passes, `member` passes, `viewer` — a real,
    /// invitable role (WorkspaceSettingsView's own invite picker offers
    /// it) — is refused, matching every route above exactly.
    ///
    /// An UNRESOLVED own role (no row for `ownUserId` in `members` yet —
    /// the members fetch hasn't landed, or failed) reads as `false`, the
    /// same "cannot tell, so do not guess towards a control that might not
    /// work" default `DocumentAuthoring.canWrite` already takes, chosen
    /// deliberately over the alternative (assume writable) because a
    /// wrongly-shown control fails LOUDLY and CONFUSINGLY for a real
    /// viewer ("Couldn't save", after they already typed something),
    /// while a wrongly-hidden one for a legitimate member costs at most
    /// one refresh cycle. That asymmetry is real but small in practice:
    /// `members` hydrates from the SAME disk cache and the SAME concurrent
    /// refresh as `tasks` (WorkspaceStore.bind/refresh), so by the time a
    /// task is on screen at all, the caller's own membership row is almost
    /// always already known — and the moment a legitimate member's role
    /// does resolve, these controls appear with no further action needed.
    static func canWrite(members: [WorkspaceMember], ownUserId: String?) -> Bool {
        guard let ownUserId, !ownUserId.isEmpty else { return false }
        guard let role = members.first(where: { $0.userId == ownUserId })?.role else { return false }
        return role == "owner" || role == "member"
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

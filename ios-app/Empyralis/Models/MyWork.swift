import Foundation

/// WHAT LANDS IN "MY WORK" — a port of the web's
/// `frontend/lib/workspace/fleet/my-work.ts`, kept as a pure module with its
/// own plain XCTest for the same reason that one is: the expected set and
/// the actual set must never come from one place.
///
/// The question the surface exists to answer is "what is assigned to ME,
/// across every project" — which on iOS had no surface at all: you opened
/// each project and scanned its list. Projects tells you where work lives;
/// nothing told you what was yours.
///
/// TWO BUCKETS, NEVER ONE. Founder's decision, made before the web version
/// was built: agent-assigned work shows here too, CLEARLY MARKED. "A person
/// still needs to see what their agents owe them; hiding it would make My
/// work a lie by omission." So the split IS the product, not a formatting
/// choice — merging them would say an agent's task is a thing the reader has
/// to go do, and dropping them would say there is nothing outstanding when
/// there is.
///
///   mine   assigneeUserId == me
///          Work a person accepted. Unambiguous: the backend's own
///          project_tasks_single_assignee_check guarantees a task carries
///          EITHER a human assignee or an agent one, never both.
///
///   agent  assigned to an agent, AND createdBy == me
///          "What my agents owe me" — work I HANDED OVER. The `createdBy`
///          half is what makes this honest rather than noisy, and it is the
///          narrowest rule the data actually supports: `Agent` carries no
///          owner/creator field at all (checked on this platform too — the
///          model is id/label/agentKind/channel/currentRunId/stopped), so
///          "agents that are mine" is NOT expressible today. Dropping the
///          createdBy clause would silently turn this into "every
///          agent-assigned task in every project I can see", i.e. a
///          teammate's agents' work filed under MY name. If an owner field
///          ever lands on the agent record, widen this to `mine-or-my-agents`
///          THERE and delete this note — do not widen it by removing a
///          clause.
///
/// Everything else is deliberately absent. Unassigned/backlog tasks are NOT
/// my work: the backend's `list_my_tasks` folds them in (an agent asking
/// "what can I pick up" genuinely wants unclaimed work), but a person's own
/// "My work" is an ownership list, not a job board — a project's own task
/// list is where unclaimed work is browsed. Tasks I merely CREATED and
/// handed to another PERSON are theirs, not mine.
///
/// NOT a second backend query, on this platform either. `WorkspaceStore`
/// already holds `GET /api/w/{ws}/fleet/tasks` with no `project_id`, which
/// returns every task in every project the caller can see with
/// `assignee_user_id` / `assignee_agent_id` / `created_by` on every row — so
/// this screen reads from the local store like every other one and never
/// awaits a fetch to render.

/// Structural on purpose (a protocol, not a concrete type) so this module
/// depends on nothing — the real `EmpTask` satisfies it, and so does a test
/// fixture.
protocol MyWorkTaskShape {
    var status: String { get }
    var assigneeUserId: String? { get }
    var assigneeAgentId: String? { get }
    var createdBy: String? { get }
}

enum MyWorkBucket: String, Equatable {
    case mine
    case agent
}

/// The one place the rule lives. `nil` means "not my work" — every caller
/// (the list, the count, the test) goes through this, so a fourth reader
/// cannot invent a fifth interpretation.
func myWorkBucket(_ task: any MyWorkTaskShape, userId: String?) -> MyWorkBucket? {
    let me = (userId ?? "").trimmingCharacters(in: .whitespaces)
    guard !me.isEmpty else { return nil }
    if (task.assigneeUserId ?? "").trimmingCharacters(in: .whitespaces) == me { return .mine }
    let agentAssignee = (task.assigneeAgentId ?? "").trimmingCharacters(in: .whitespaces)
    if !agentAssignee.isEmpty, (task.createdBy ?? "").trimmingCharacters(in: .whitespaces) == me { return .agent }
    return nil
}

/// A task still needing someone's attention. `done` is the only terminal
/// status in the vocabulary — `blocked`/`awaiting_input`/`in_review` all
/// describe work that is still open, and burying them would be the same
/// family of lie as collapsing "empty" into "couldn't load": "nothing to do"
/// is not the same as "nothing left to finish".
func isOpenMyWork(_ task: any MyWorkTaskShape) -> Bool {
    task.status.trimmingCharacters(in: .whitespaces).lowercased() != "done"
}

/// Both buckets in one pass, input order preserved within each.
func selectMyWork<T: MyWorkTaskShape>(_ tasks: [T], userId: String?) -> (mine: [T], agent: [T]) {
    var mine: [T] = []
    var agent: [T] = []
    for task in tasks {
        switch myWorkBucket(task, userId: userId) {
        case .mine: mine.append(task)
        case .agent: agent.append(task)
        case nil: continue
        }
    }
    return (mine, agent)
}

/// Counts OPEN items in BOTH buckets — any badge and the screen's contents
/// have to agree, and a count that silently omitted the agent half would
/// understate exactly the thing the founder asked to be shown. Zero renders
/// no badge at all (the caller's job): a zero badge is noise, and this
/// returning 0 is how it says so.
func myWorkBadgeCount(_ tasks: [any MyWorkTaskShape], userId: String?) -> Int {
    tasks.filter { myWorkBucket($0, userId: userId) != nil && isOpenMyWork($0) }.count
}

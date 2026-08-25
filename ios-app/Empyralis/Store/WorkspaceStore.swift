import Foundation
import SwiftUI

/// THE LOCAL-FIRST CORE. This is what makes the app feel like Linear rather
/// than like a website in a native shell, and the rule it enforces is
/// simple: A SCREEN NEVER AWAITS A NETWORK CALL TO RENDER.
///
///   init            hydrate from disk SYNCHRONOUSLY, so the very first
///                   frame after launch already has real content
///   onAppear        kick a background refresh; the list is already on
///                   screen, so a slow network changes nothing visually
///   a write         apply to the in-memory model IMMEDIATELY, send the
///                   request after, roll back only if the server refuses
///
/// The honesty law from CLAUDE.md applies to the loading states here, and
/// it is why `hasLoadedOnce` exists separately from `isRefreshing`: "we
/// have nothing yet" and "we have data and are checking for more" are
/// different facts, and only the first one may ever show a skeleton. A
/// spinner over content we already hold is a lie about what we know.
@MainActor
final class WorkspaceStore: ObservableObject {
    @Published private(set) var tasks: [EmpTask] = []
    @Published private(set) var projects: [Project] = []
    @Published private(set) var agents: [Agent] = []

    /// The workspace's label VOCABULARY (not a task's labels). Attaching is
    /// curated — `attach_label` refuses a name that is not already in this
    /// list rather than minting it — so an empty vocabulary means there is
    /// genuinely nothing to attach, and the detail view renders its label
    /// chips read-only instead of offering a picker that can only fail.
    @Published private(set) var labelVocabulary: [TaskLabel] = []

    /// Valid HUMAN assignees, and the lookup that turns a `user_id` on a
    /// comment or an attribution row into a name.
    @Published private(set) var members: [WorkspaceMember] = []

    /// THE TWO INBOX SOURCES THAT ARE NOT TASKS. Held here, cached to disk
    /// and refreshed with everything else for one reason: the Inbox is a
    /// list screen, and a list screen that awaits a fetch to render is the
    /// regression this whole class exists to prevent. See InboxNeedsYou.swift
    /// for what each source is and why there are exactly three.
    @Published private(set) var notifications: [FleetNotification] = []
    @Published private(set) var blockedRuns: [WorkspaceActivityEvent] = []

    /// PER-SOURCE, and deliberately not folded into `loadError`. The Inbox
    /// composes three independent reads, so "notifications failed" has to be
    /// sayable next to a task section that loaded perfectly — collapsing
    /// them would render a failed source as "this section has zero items",
    /// which is the empty-vs-couldn't-load lie one level down. Set on
    /// failure, cleared on success, and the last-good rows stay on screen
    /// either way (they are still true, just possibly not the newest).
    @Published private(set) var notificationsError: String?
    @Published private(set) var blockedRunsError: String?

    /// True once ANY source — disk or network — has populated this store.
    /// Skeletons key off this, never off an in-flight request.
    @Published private(set) var hasLoadedOnce = false
    @Published private(set) var isRefreshing = false

    /// THE FOURTH FACT, and it is not a nicety. When the members/agents
    /// lookup itself FAILS (a blip, an expiring session) every actor on a
    /// task resolves to nothing — and rendering that as "Someone" is
    /// indistinguishable from a genuinely anonymous system action. The web
    /// hit this live on 2026-08-13: a task's own creator vanished from
    /// "Created by" moments after creating it. "Nobody is assigned" and "I
    /// could not find out who is assigned" are different facts and never
    /// share one label.
    @Published private(set) var identityLookupFailed = false

    /// Set when a refresh genuinely failed AND we have nothing cached to
    /// show. With cached content present a failure is deliberately silent —
    /// the content on screen is still true, just possibly not the newest.
    @Published var loadError: String?

    private var workspaceId: String?
    private unowned let session: SessionStore

    init(session: SessionStore) {
        self.session = session
    }

    /// Binds the store to a workspace and hydrates from disk on the spot.
    /// Synchronous by design — an async hydrate would flash an empty list
    /// for one frame, which is the exact jank this whole class exists to
    /// prevent.
    func bind(workspaceId: String) {
        guard self.workspaceId != workspaceId else { return }
        self.workspaceId = workspaceId

        if let cached = DiskCache.load(CachedWorkspace.self, from: "workspace-\(workspaceId)") {
            tasks = cached.tasks
            projects = cached.projects
            agents = cached.agents
            labelVocabulary = cached.labels
            members = cached.members
            notifications = cached.notifications
            blockedRuns = cached.blockedRuns
            hasLoadedOnce = true
        }
    }

    func refresh() async {
        guard let workspaceId else { return }
        isRefreshing = true
        defer { isRefreshing = false }

        // Fired concurrently, not in sequence — five round trips run in the
        // time of the slowest one, not their sum.
        async let tasksResult: TasksResponse? = try? APIClient.shared.get(
            "/w/\(workspaceId)/fleet/tasks", query: ["sort": "priority"]
        )
        async let projectsResult: ProjectsResponse? = try? APIClient.shared.get(
            "/w/\(workspaceId)/fleet/projects"
        )
        async let agentsResult: AgentsResponse? = try? APIClient.shared.get(
            "/w/\(workspaceId)/fleet/agents"
        )
        async let labelsResult: LabelsResponse? = try? APIClient.shared.get(
            "/w/\(workspaceId)/fleet/labels"
        )
        // NOT under /w/{id}/fleet — workspace membership is its own router,
        // and it answers {"items": [...]} rather than the fleet envelope.
        async let membersResult: MembersResponse? = try? APIClient.shared.get(
            "/workspaces/\(workspaceId)/members"
        )
        // The two Inbox sources. `nonisolated static` so they actually run
        // concurrently — a @MainActor instance method inside `async let`
        // would serialize on the main actor and turn seven parallel round
        // trips back into seven sequential ones.
        async let notificationsResult = Self.fetchNotifications(workspaceId: workspaceId)
        async let blockedRunsResult = Self.fetchBlockedRuns(workspaceId: workspaceId)

        let (t, p, a, l, m) = await (tasksResult, projectsResult, agentsResult, labelsResult, membersResult)
        let (n, r) = await (notificationsResult, blockedRunsResult)

        // A partial failure updates what DID come back rather than discarding
        // the whole refresh — one dead endpoint must not blank two healthy
        // surfaces.
        var changed = false
        if let t, t.ok { tasks = t.tasks; changed = true }
        if let p, p.ok { projects = p.projects; changed = true }
        if let a, a.ok { agents = a.agents; changed = true }
        if let l, l.ok { labelVocabulary = l.labels; changed = true }

        // Identity is the one lookup whose FAILURE has to be remembered
        // rather than degraded into an empty list — see identityLookupFailed.
        if let m {
            members = m.items
            identityLookupFailed = false
            changed = true
        } else {
            identityLookupFailed = true
        }

        // Same posture as the web's own Inbox sources: a failed poll keeps
        // the last-good rows and reports the failure beside them, rather
        // than blanking a section that was true a minute ago.
        switch n {
        case .loaded(let items):
            notifications = items
            notificationsError = nil
            changed = true
        case .failed(let message):
            notificationsError = message
        }

        switch r {
        case .loaded(let items):
            blockedRuns = items
            blockedRunsError = nil
            changed = true
        case .failed(let message):
            blockedRunsError = message
        }

        if changed {
            hasLoadedOnce = true
            loadError = nil
            persist()
        } else if !hasLoadedOnce {
            loadError = "Couldn't reach Empyralis. Pull to retry."
        }
    }

    // MARK: - Inbox sources

    /// `GET /api/w/{ws}/fleet/notifications?limit=50&unread_only=true` — the
    /// caller's own feed, recipient-scoped by the route itself. `unread_only`
    /// mirrors the web's default: a "needs you" surface only ever wants
    /// notifications nobody has acted on yet.
    ///
    /// Returns a `SourceLoad` rather than an optional because the caller has
    /// to be able to tell an empty feed from an unreachable one — the route answers
    /// HTTP 200 with `ok:false` on a service-level failure, so a bare array
    /// would report "you're all caught up" about a read that never happened.
    private nonisolated static func fetchNotifications(
        workspaceId: String
    ) async -> SourceLoad<[FleetNotification]> {
        do {
            let response: NotificationsResponse = try await APIClient.shared.get(
                "/w/\(workspaceId)/fleet/notifications",
                query: ["limit": "50", "unread_only": "true"]
            )
            guard response.ok else {
                return .failed(response.error ?? "Couldn't load your notifications.")
            }
            return .loaded(response.notifications)
        } catch APIError.server(let message) {
            return .failed(message)
        } catch {
            return .failed("Couldn't load your notifications.")
        }
    }

    /// `GET /api/activity/timeline?workspace_id=…&event_class=blocked_action`.
    ///
    /// TWO THINGS ABOUT THIS PATH ARE EASY TO GET WRONG. It is NOT under
    /// `/w/{id}/fleet` — it is a top-level route taking `workspace_id` as a
    /// query parameter (access is still enforced inside, at viewer). And the
    /// filter is `event_class`, the STRUCTURAL field, never a text match on
    /// the title: `run_failed` / `machine_revoked` /
    /// `machine_enrollment_failed` are classified into `blocked_action`
    /// server-side, and their human-readable titles change without notice.
    private nonisolated static func fetchBlockedRuns(
        workspaceId: String
    ) async -> SourceLoad<[WorkspaceActivityEvent]> {
        do {
            let response: ActivityTimelineResponse = try await APIClient.shared.get(
                "/activity/timeline",
                query: [
                    "workspace_id": workspaceId,
                    "limit": "30",
                    "event_class": "blocked_action",
                ]
            )
            return .loaded(response.items)
        } catch APIError.server(let message) {
            return .failed(message)
        } catch {
            return .failed("Couldn't load failed runs.")
        }
    }

    /// `POST /api/w/{ws}/fleet/notifications/{id}/read`.
    ///
    /// Optimistic like every other write here: the feed is fetched
    /// `unread_only`, so a read notification simply leaves the list. On a
    /// refusal it goes back where it was — losing someone's notification
    /// because a request failed is strictly worse than showing it twice.
    ///
    /// The WHERE clause behind this is recipient-scoped server-side, so
    /// there is no ownership check to make on this side beyond passing the
    /// id through.
    func markNotificationRead(_ notificationId: String) async {
        guard let workspaceId else { return }
        guard let index = notifications.firstIndex(where: { $0.id == notificationId }) else { return }

        let removed = notifications[index]
        notifications.remove(at: index)
        persist()

        do {
            let ack: NotificationReadAck = try await APIClient.shared.post(
                "/w/\(workspaceId)/fleet/notifications/\(notificationId)/read", body: [:]
            )
            if !ack.ok { restore(removed, at: index) }
        } catch APIError.decoding {
            // 2xx. APIClient only throws .decoding AFTER its status guard
            // passes, so the server accepted and acted; only the reply was
            // unreadable. Restoring here would put back a notification the
            // server has already marked read.
            return
        } catch {
            restore(removed, at: index)
        }
    }

    private func restore(_ notification: FleetNotification, at index: Int) {
        guard !notifications.contains(where: { $0.id == notification.id }) else { return }
        notifications.insert(notification, at: min(index, notifications.count))
        persist()
    }

    // MARK: - The one write path

    /// EVERY task mutation crosses this function, so the optimistic-apply,
    /// the rollback, and — most of all — the honesty of the message all live
    /// in exactly one place rather than being re-derived per call site. The
    /// per-branch version of this is the shape CLAUDE.md records as "a guard
    /// called once inside a large function is a guard the next branch will
    /// skip".
    ///
    /// THREE OUTCOMES, NEVER TWO. The distinction that matters is the last
    /// one, and it is the reason this is not a plain try/catch:
    ///
    ///   ok:false            the server READ the request and said no.
    ///                       Roll back and repeat its reason verbatim.
    ///   non-2xx             refused at the HTTP layer. Same treatment.
    ///   2xx, undecodable    THE WRITE LANDED. APIClient only ever throws
    ///                       .decoding AFTER its 2xx guard passes, so the
    ///                       server accepted and acted; we merely could not
    ///                       read the reply. Rolling back here would be the
    ///                       lie — it would show the person their change
    ///                       being undone while the server holds it.
    ///   no response at all  we cannot tell. Roll back to last-known-true
    ///                       and SAY we could not confirm, rather than
    ///                       claiming a failure we did not observe.
    private func commit(
        taskId: String,
        optimistic: (inout EmpTask) -> Void,
        write: @escaping () async throws -> (ok: Bool, error: String?)
    ) async -> String? {
        guard workspaceId != nil else { return "No workspace." }
        guard let index = tasks.firstIndex(where: { $0.id == taskId }) else { return nil }

        let previous = tasks[index]
        var next = previous
        optimistic(&next)
        tasks[index] = next
        persist()

        do {
            let ack = try await write()
            if !ack.ok {
                rollback(taskId: taskId, to: previous, from: next)
                return ack.error ?? "Couldn't save that change."
            }
            return nil
        } catch APIError.unauthorized {
            await session.handleUnauthorized()
            rollback(taskId: taskId, to: previous, from: next)
            return nil
        } catch APIError.server(let message) {
            rollback(taskId: taskId, to: previous, from: next)
            return message
        } catch APIError.decoding {
            // 2xx. The change is real; only the response body was unreadable.
            // Keep it and let the next refresh reconcile the exact row.
            Task { await self.refresh() }
            return nil
        } catch {
            rollback(taskId: taskId, to: previous, from: next)
            Task { await self.refresh() }
            return "Couldn't confirm that change. Check your connection and pull to refresh."
        }
    }

    /// Undo an optimistic change AFTER an await, which is the only place this
    /// is subtle. Two things can have happened while the request was in
    /// flight, and the naive version gets both wrong:
    ///
    ///   the array RE-ORDERED   a refresh landed, so the index captured
    ///                          before the await now points at a DIFFERENT
    ///                          task — writing to it would corrupt an
    ///                          unrelated row. Hence the re-lookup by id.
    ///   the row MOVED ON       a refresh landed a newer copy of this same
    ///                          task. That copy came from the server and is
    ///                          more true than our pre-write snapshot, so
    ///                          restoring the snapshot would silently undo
    ///                          someone else's real change. We therefore
    ///                          only ever undo a row that still holds
    ///                          exactly what WE put there.
    private func rollback(taskId: String, to previous: EmpTask, from optimistic: EmpTask) {
        guard let index = tasks.firstIndex(where: { $0.id == taskId }) else { return }
        guard tasks[index] == optimistic else { return }
        tasks[index] = previous
        persist()
    }

    // MARK: - Optimistic writes

    /// PATCH /fleet/tasks/{id} — the one endpoint that edits a task's own
    /// fields. Requires `member`, so a viewer's write is refused server-side
    /// and rolled back here rather than being hidden by an optimistic update
    /// that never reconciles.
    func setTaskStatus(_ task: EmpTask, to status: String) async -> String? {
        guard let workspaceId else { return "No workspace." }
        return await commit(taskId: task.id) { $0.status = status } write: {
            let ack: TaskWriteAck = try await APIClient.shared.patch(
                "/w/\(workspaceId)/fleet/tasks/\(task.id)", body: ["status": status]
            )
            return (ack.ok, ack.error)
        }
    }

    /// Same PATCH route. `0` is a real value here ("clear the priority"),
    /// which is why the field is sent unconditionally rather than omitted
    /// when falsy — omitting it means "leave untouched" server-side.
    func setTaskPriority(_ task: EmpTask, to priority: TaskPriority) async -> String? {
        guard let workspaceId else { return "No workspace." }
        return await commit(taskId: task.id) { $0.priority = priority.rawValue } write: {
            let ack: TaskWriteAck = try await APIClient.shared.patch(
                "/w/\(workspaceId)/fleet/tasks/\(task.id)", body: ["priority": priority.rawValue]
            )
            return (ack.ok, ack.error)
        }
    }

    /// Clearing a due date is `clear_due_at: true`, NOT `due_at: null` — a
    /// null there reads as "leave untouched", so the two have to be sent as
    /// different requests rather than one nullable field.
    func setTaskDueDate(_ task: EmpTask, to dueAt: String?) async -> String? {
        guard let workspaceId else { return "No workspace." }
        let body: [String: Any] = dueAt.map { ["due_at": $0] } ?? ["clear_due_at": true]
        return await commit(taskId: task.id) { $0.dueAt = dueAt } write: {
            let ack: TaskWriteAck = try await APIClient.shared.patch(
                "/w/\(workspaceId)/fleet/tasks/\(task.id)", body: body
            )
            return (ack.ok, ack.error)
        }
    }

    /// POST /fleet/tasks/{id}/assign. Assignment is deliberately NOT part of
    /// the PATCH above — it is its own route because assigning an AGENT also
    /// schedules that agent's wakeup.
    ///
    /// Exactly one of agent/user, never both, and never neither: the endpoint
    /// refuses an empty body with "agent_id or user_id is required", so there
    /// is no unassign to offer here.
    func assignTask(_ task: EmpTask, toAgent agentId: String) async -> String? {
        guard let workspaceId else { return "No workspace." }
        return await commit(taskId: task.id) {
            $0.assigneeAgentId = agentId
            $0.assigneeUserId = nil
            $0.assigneeType = "agent"
            // assign_task flips an unstarted task to in_progress server-side;
            // mirroring that here keeps the optimistic row honest instead of
            // showing a stale "Todo" until the next refresh.
            if ["backlog", "todo", "open"].contains($0.status) { $0.status = "in_progress" }
        } write: {
            let ack: TaskWriteAck = try await APIClient.shared.post(
                "/w/\(workspaceId)/fleet/tasks/\(task.id)/assign", body: ["agent_id": agentId]
            )
            return (ack.ok, ack.error)
        }
    }

    /// The human half of the same route. Assigning a PERSON never schedules
    /// a wakeup — people are not woken by schedulers — and never moves the
    /// status, so this branch deliberately does neither.
    func assignTask(_ task: EmpTask, toMember userId: String) async -> String? {
        guard let workspaceId else { return "No workspace." }
        return await commit(taskId: task.id) {
            $0.assigneeUserId = userId
            $0.assigneeAgentId = nil
            $0.assigneeType = "user"
        } write: {
            let ack: TaskWriteAck = try await APIClient.shared.post(
                "/w/\(workspaceId)/fleet/tasks/\(task.id)/assign", body: ["user_id": userId]
            )
            return (ack.ok, ack.error)
        }
    }

    /// POST /fleet/tasks/{id}/labels. Idempotent server-side, and it refuses
    /// to mint a label that is not already in the workspace vocabulary — so
    /// the caller must pass something from `labelVocabulary`, never free text.
    func attachLabel(_ task: EmpTask, label: TaskLabel) async -> String? {
        guard let workspaceId else { return "No workspace." }
        guard !task.labels.contains(where: { $0.id == label.id }) else { return nil }
        return await commit(taskId: task.id) { $0.labels.append(label) } write: {
            let ack: TaskWriteAck = try await APIClient.shared.post(
                "/w/\(workspaceId)/fleet/tasks/\(task.id)/labels", body: ["label": label.id]
            )
            return (ack.ok, ack.error)
        }
    }

    /// DELETE /fleet/tasks/{id}/labels/{label}. Idempotent, and the label
    /// itself survives — this removes the link, never the vocabulary entry.
    func detachLabel(_ task: EmpTask, label: TaskLabel) async -> String? {
        guard let workspaceId else { return "No workspace." }
        return await commit(taskId: task.id) { copy in
            copy.labels.removeAll { $0.id == label.id }
        } write: {
            let ack: TaskWriteAck = try await TaskWriteAPI.delete(
                "/w/\(workspaceId)/fleet/tasks/\(task.id)/labels/\(label.id)"
            )
            return (ack.ok, ack.error)
        }
    }

    /// POST /fleet/tasks/{id}/comments — the human->agent channel, and the
    /// one genuine write on this screen that creates something rather than
    /// editing it.
    ///
    /// The optimistic comment carries a locally-minted id; the server mints
    /// its own (`comment_<hex12>`) and the next refresh swaps ours out. That
    /// is deliberate rather than clever: the alternative is a composer that
    /// sits empty for a round trip on a connection this product's own
    /// measurements put at multiple seconds from where the founder sits.
    func postComment(_ task: EmpTask, body: String, authorUserId: String?) async -> String? {
        guard let workspaceId else { return "No workspace." }
        let text = body.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        // 4000 mirrors the route's own max_length — refusing here is the same
        // fact the server would state, minus a round trip.
        guard text.count <= 4000 else {
            return "That comment is too long. The limit is 4000 characters."
        }

        let pending = TaskComment.local(body: text, authorUserId: authorUserId)
        return await commit(taskId: task.id) { $0.metadata.comments.append(pending) } write: {
            let ack: TaskWriteAck = try await APIClient.shared.post(
                "/w/\(workspaceId)/fleet/tasks/\(task.id)/comments", body: ["body": text]
            )
            return (ack.ok, ack.error)
        }
    }

    // MARK: - Reads

    func task(_ id: String) -> EmpTask? {
        tasks.first { $0.id == id }
    }

    func tasks(inProject projectId: String) -> [EmpTask] {
        tasks.filter { $0.projectId == projectId }
    }

    /// A sub-task is a plain row in the same table, and the task list is not
    /// filtered to top-level, so every sub-task this caller may see is
    /// ALREADY in `tasks`. Deriving them locally keeps the detail view on the
    /// local-first contract — no screen waits on a network call to render —
    /// where a GET .../subtasks round trip would break it for no new data.
    ///
    /// The authoritative COUNT still comes off the parent's own rollup, so a
    /// caller can tell "this task has no sub-tasks" from "I am not holding
    /// all of them yet" instead of quietly showing a short list as complete.
    func subtasks(of parentId: String) -> [EmpTask] {
        tasks.filter { $0.parentTaskId == parentId }
    }

    /// The Operator/Sage install is excluded, matching the web app's own
    /// "real agent count" rule — it exists in every workspace and is not
    /// an agent anyone created.
    var realAgents: [Agent] {
        agents.filter { $0.agentKind != "master" }
    }

    func agent(_ id: String) -> Agent? {
        agents.first { $0.id == id }
    }

    func member(_ userId: String) -> WorkspaceMember? {
        members.first { $0.userId == userId }
    }

    func clear() {
        tasks = []
        projects = []
        agents = []
        labelVocabulary = []
        members = []
        notifications = []
        blockedRuns = []
        notificationsError = nil
        blockedRunsError = nil
        identityLookupFailed = false
        hasLoadedOnce = false
        workspaceId = nil
        DiskCache.clearAll()
    }

    private func persist() {
        guard let workspaceId else { return }
        DiskCache.save(
            CachedWorkspace(
                tasks: tasks,
                projects: projects,
                agents: agents,
                labels: labelVocabulary,
                members: members,
                notifications: notifications,
                blockedRuns: blockedRuns
            ),
            as: "workspace-\(workspaceId)"
        )
    }
}

struct CachedWorkspace: Codable {
    let tasks: [EmpTask]
    let projects: [Project]
    let agents: [Agent]
    // Added alongside the task-detail surfaces. A cache written by a build
    // predating them simply fails to decode and is treated as no cache —
    // DiskCache.load already returns an optional — so this self-heals on the
    // first refresh rather than needing a migration.
    let labels: [TaskLabel]
    let members: [WorkspaceMember]

    // The two Inbox sources, added when the phone's Inbox stopped being a
    // list of open tasks and started answering the same question the web's
    // does. Defaulted rather than required so a cache written by the build
    // before them still decodes — the self-healing above works, but silently
    // throwing away someone's whole cached workspace to add two lists is a
    // cold-start regression for no reason.
    let notifications: [FleetNotification]
    let blockedRuns: [WorkspaceActivityEvent]

    init(
        tasks: [EmpTask],
        projects: [Project],
        agents: [Agent],
        labels: [TaskLabel],
        members: [WorkspaceMember],
        notifications: [FleetNotification] = [],
        blockedRuns: [WorkspaceActivityEvent] = []
    ) {
        self.tasks = tasks
        self.projects = projects
        self.agents = agents
        self.labels = labels
        self.members = members
        self.notifications = notifications
        self.blockedRuns = blockedRuns
    }

    enum CodingKeys: String, CodingKey {
        case tasks, projects, agents, labels, members, notifications, blockedRuns
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tasks = try c.decode([EmpTask].self, forKey: .tasks)
        projects = try c.decode([Project].self, forKey: .projects)
        agents = try c.decode([Agent].self, forKey: .agents)
        labels = try c.decode([TaskLabel].self, forKey: .labels)
        members = try c.decode([WorkspaceMember].self, forKey: .members)
        notifications = (try? c.decodeIfPresent([FleetNotification].self, forKey: .notifications)).flatMap { $0 } ?? []
        blockedRuns = (try? c.decodeIfPresent([WorkspaceActivityEvent].self, forKey: .blockedRuns)).flatMap { $0 } ?? []
    }
}

/// Deliberately NOT `Result<T, Error>`. What every caller of this actually
/// needs is a MESSAGE it can put on screen, not a thrown type to
/// re-classify — and two named cases keep "this source is empty" and "this
/// source could not be read" different facts the whole way from the request
/// to the row, which is the one thing collapsing them would destroy.
enum SourceLoad<T> {
    case loaded(T)
    case failed(String)
}

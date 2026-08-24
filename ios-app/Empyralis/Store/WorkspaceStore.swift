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

    /// True once ANY source — disk or network — has populated this store.
    /// Skeletons key off this, never off an in-flight request.
    @Published private(set) var hasLoadedOnce = false
    @Published private(set) var isRefreshing = false

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
            hasLoadedOnce = true
        }
    }

    func refresh() async {
        guard let workspaceId else { return }
        isRefreshing = true
        defer { isRefreshing = false }

        // Fired concurrently, not in sequence — three round trips run in the
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

        let (t, p, a) = await (tasksResult, projectsResult, agentsResult)

        // A partial failure updates what DID come back rather than discarding
        // the whole refresh — one dead endpoint must not blank two healthy
        // surfaces.
        var changed = false
        if let t, t.ok { tasks = t.tasks; changed = true }
        if let p, p.ok { projects = p.projects; changed = true }
        if let a, a.ok { agents = a.agents; changed = true }

        if changed {
            hasLoadedOnce = true
            loadError = nil
            persist()
        } else if !hasLoadedOnce {
            loadError = "Couldn't reach Empyralis. Pull to retry."
        }
    }

    // MARK: - Optimistic writes

    /// Applies the new status instantly, then confirms with the server.
    /// On refusal the old value is restored AND the reason surfaced — a
    /// silent rollback would look like the tap simply didn't register.
    func setTaskStatus(_ task: EmpTask, to status: String) async -> String? {
        guard let workspaceId else { return "No workspace." }
        guard let index = tasks.firstIndex(where: { $0.id == task.id }) else { return nil }

        let previous = tasks[index]
        tasks[index] = previous.withStatus(status)
        persist()

        struct Ack: Decodable { let ok: Bool; let error: String? }
        do {
            // PATCH /fleet/tasks/{id} — the one endpoint that edits a task's
            // own fields. Requires `member`, so a viewer's write is refused
            // server-side and rolled back here rather than being hidden by
            // an optimistic update that never reconciles.
            let ack: Ack = try await APIClient.shared.patch(
                "/w/\(workspaceId)/fleet/tasks/\(task.id)",
                body: ["status": status]
            )
            if !ack.ok {
                tasks[index] = previous
                persist()
                return ack.error ?? "Couldn't update that task."
            }
            return nil
        } catch APIError.unauthorized {
            await session.handleUnauthorized()
            tasks[index] = previous
            persist()
            return nil
        } catch {
            tasks[index] = previous
            persist()
            return "Couldn't update that task. Check your connection."
        }
    }

    func tasks(inProject projectId: String) -> [EmpTask] {
        tasks.filter { $0.projectId == projectId }
    }

    /// The Operator/Sage install is excluded, matching the web app's own
    /// "real agent count" rule — it exists in every workspace and is not
    /// an agent anyone created.
    var realAgents: [Agent] {
        agents.filter { $0.agentKind != "master" }
    }

    func clear() {
        tasks = []
        projects = []
        agents = []
        hasLoadedOnce = false
        workspaceId = nil
        DiskCache.clearAll()
    }

    private func persist() {
        guard let workspaceId else { return }
        DiskCache.save(
            CachedWorkspace(tasks: tasks, projects: projects, agents: agents),
            as: "workspace-\(workspaceId)"
        )
    }
}

struct CachedWorkspace: Codable {
    let tasks: [EmpTask]
    let projects: [Project]
    let agents: [Agent]
}

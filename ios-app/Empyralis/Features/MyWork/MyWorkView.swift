import SwiftUI

/// MY WORK — "what is assigned to me, across every project."
///
/// The web has had this as a top-level rail item since 2026-08-15; the phone
/// did not have it at all, so the question had no surface here: you opened
/// each project in turn and scanned its list. Projects tells you where work
/// lives; nothing told you what was yours.
///
/// TWO SECTIONS, NEVER ONE LIST — the rule is `MyWork.swift`, ported from the
/// web's `my-work.ts` and never re-derived here. Read that file for why the
/// agent bucket needs its `createdBy` clause and why merging or dropping
/// either half is a documented mistake.
///
/// NO NEW BACKEND, on this platform either. `WorkspaceStore` already holds
/// `GET /api/w/{ws}/fleet/tasks` with no `project_id` — every task in every
/// project this caller can see, project-visibility filtered server-side —
/// so this screen renders from the local store on its first frame like every
/// other list here.
///
/// A task's home is its project. Every row pushes the SAME `TaskDetailView`
/// the project list pushes, so this surface is a lens, never a second place
/// a task lives.
struct MyWorkView: View {
    @EnvironmentObject private var store: WorkspaceStore
    @EnvironmentObject private var session: SessionStore
    @Environment(\.colorScheme) private var scheme

    /// Done work stays reachable but never leads: an ownership list that
    /// opens on finished items is answering yesterday's question.
    @State private var showDone = false

    private var myUserId: String? { session.user?.id }

    private var buckets: (mine: [EmpTask], agent: [EmpTask]) {
        selectMyWork(store.tasks, userId: myUserId)
    }

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.bgPage(scheme).ignoresSafeArea()
                content
            }
            .navigationTitle("My work")
            .toolbar { doneToggle }
            .navigationDestination(for: TaskRoute.self) { route in
                TaskDetailView(taskId: route.taskId)
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        let buckets = buckets
        let openMine = buckets.mine.filter(isOpenMyWork)
        let openAgent = buckets.agent.filter(isOpenMyWork)
        let shownMine = showDone ? buckets.mine : openMine
        let shownAgent = showDone ? buckets.agent : openAgent

        if !store.hasLoadedOnce {
            if let error = store.loadError {
                ScrollView {
                    EmptyStateView(
                        title: "Couldn't load your work",
                        message: error,
                        systemImage: "wifi.exclamationmark"
                    )
                    .padding(.top, Space.x10)
                }
                .refreshable { await store.refresh() }
            } else {
                List {
                    ForEach(0..<5, id: \.self) { _ in
                        SkeletonRow().listRowBackground(Theme.bgPage(scheme))
                    }
                }
                .listStyle(.plain)
            }
        } else if myUserId == nil {
            // Both buckets key off the caller's own id. Without it the
            // honest answer is "I can't tell", never an empty list that
            // reads as "nothing is assigned to you".
            ScrollView {
                EmptyStateView(
                    title: "Couldn't tell what's yours",
                    message: "Your account didn't come back with this session. Pull to retry.",
                    systemImage: "person.crop.circle.badge.exclamationmark"
                )
                .padding(.top, Space.x10)
            }
            .refreshable { await store.refresh() }
        } else if shownMine.isEmpty && shownAgent.isEmpty {
            ScrollView {
                EmptyStateView(
                    title: showDone ? "Nothing here yet" : "Nothing open",
                    message: "Work assigned to you — and work you handed to your agents — shows up here.",
                    systemImage: "checkmark.circle"
                )
                .padding(.top, Space.x10)
            }
            .refreshable { await store.refresh() }
        } else {
            List {
                section(title: "Assigned to me", tasks: shownMine, showsAgent: false)
                section(title: "With your agents", tasks: shownAgent, showsAgent: true)
            }
            .listStyle(.plain)
            .refreshable { await store.refresh() }
        }
    }

    /// Rendered only when there IS finished work to reveal — a toggle that
    /// can only ever show the same empty list is a dead control.
    @ToolbarContentBuilder
    private var doneToggle: some ToolbarContent {
        let buckets = buckets
        let total = buckets.mine.count + buckets.agent.count
        let openCount = buckets.mine.filter(isOpenMyWork).count + buckets.agent.filter(isOpenMyWork).count
        if total > openCount {
            ToolbarItem(placement: .topBarTrailing) {
                Button(showDone ? "Hide done" : "Show done") {
                    showDone.toggle()
                }
                .font(.empSecondary)
                .foregroundStyle(Theme.textSecondary(scheme))
            }
        }
    }

    /// An empty bucket renders nothing rather than an empty heading — but
    /// only ONE of the two can be empty at a time here, because the
    /// screen-level empty state above covers the both-empty case.
    @ViewBuilder
    private func section(title: String, tasks: [EmpTask], showsAgent: Bool) -> some View {
        if !tasks.isEmpty {
            Section {
                ForEach(tasks) { task in
                    NavigationLink(value: TaskRoute(taskId: task.id)) {
                        if showsAgent {
                            MyWorkAgentRow(task: task)
                        } else {
                            TaskRow(task: task)
                        }
                    }
                    .listRowBackground(Theme.bgPage(scheme))
                }
            } header: {
                HStack(spacing: Space.x2) {
                    Text(title.uppercased())
                        .font(.empSectionHeader)
                        .foregroundStyle(Theme.textMuted(scheme))
                    Text("\(tasks.count)")
                        .font(.empCaption)
                        .foregroundStyle(Theme.textMuted(scheme))
                }
            }
        }
    }
}

/// The agent bucket's row. CLEARLY MARKED is the whole point of the split —
/// the section header says these are an agent's, and the row names WHICH
/// agent, so a reader never mistakes one for something they have to go do.
///
/// A name that cannot be resolved falls back to the install id rather than
/// to the word "an agent": the id is at least a thing you can search for.
struct MyWorkAgentRow: View {
    let task: EmpTask
    @EnvironmentObject private var store: WorkspaceStore
    @Environment(\.colorScheme) private var scheme

    private var agentName: String? {
        guard let agentId = task.assigneeAgentId, !agentId.isEmpty else { return nil }
        return store.agent(agentId)?.displayName ?? agentId
    }

    var body: some View {
        HStack(alignment: .top, spacing: Space.x3) {
            StatusDot(status: task.status)
                .padding(.top, 5)

            VStack(alignment: .leading, spacing: Space.x1) {
                Text(task.title)
                    .font(.empBody)
                    .foregroundStyle(Theme.textPrimary(scheme))
                    .lineLimit(2)

                HStack(spacing: Space.x2) {
                    if let displayId = task.displayId, !displayId.isEmpty {
                        Text(displayId)
                            .font(.empMono)
                            .foregroundStyle(Theme.textMuted(scheme))
                    }
                    Text(task.statusLabel)
                        .font(.empCaption)
                        .foregroundStyle(Theme.textMuted(scheme))
                    if let agentName {
                        Text("· \(agentName)")
                            .font(.empCaption)
                            .foregroundStyle(Theme.textMuted(scheme))
                            .lineLimit(1)
                    }
                }
            }
        }
        .padding(.vertical, Space.x1)
    }
}

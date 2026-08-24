import SwiftUI

struct ProjectsView: View {
    @EnvironmentObject private var store: WorkspaceStore
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.bgPage(scheme).ignoresSafeArea()

                if !store.hasLoadedOnce {
                    List {
                        ForEach(0..<5, id: \.self) { _ in
                            SkeletonRow().listRowBackground(Theme.bgPage(scheme))
                        }
                    }
                    .listStyle(.plain)
                } else if store.projects.isEmpty {
                    ScrollView {
                        EmptyStateView(
                            title: "No projects yet",
                            message: "Create one from the web app to see it here.",
                            systemImage: "folder"
                        )
                        .padding(.top, Space.x10)
                    }
                    .refreshable { await store.refresh() }
                } else {
                    List {
                        ForEach(store.projects) { project in
                            NavigationLink {
                                ProjectTasksView(project: project)
                            } label: {
                                ProjectRow(project: project, openCount: openCount(project))
                            }
                            .listRowBackground(Theme.bgPage(scheme))
                        }
                    }
                    .listStyle(.plain)
                    .refreshable { await store.refresh() }
                }
            }
            .navigationTitle("Projects")
        }
    }

    private func openCount(_ project: Project) -> Int {
        store.tasks(inProject: project.id).filter { !$0.isDone }.count
    }
}

struct ProjectRow: View {
    let project: Project
    let openCount: Int
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        HStack {
            Text(project.name)
                .font(.empBody)
                .foregroundStyle(Theme.textPrimary(scheme))
            Spacer()
            // Rendered only when there IS something open — a "0" badge is
            // noise on every row that has nothing to say.
            if openCount > 0 {
                Text("\(openCount)")
                    .font(.empCaptionMedium)
                    .foregroundStyle(Theme.textMuted(scheme))
            }
        }
        .padding(.vertical, Space.x1)
    }
}

struct ProjectTasksView: View {
    let project: Project
    @EnvironmentObject private var store: WorkspaceStore
    @Environment(\.colorScheme) private var scheme

    private var tasks: [EmpTask] {
        store.tasks(inProject: project.id)
    }

    var body: some View {
        ZStack {
            Theme.bgPage(scheme).ignoresSafeArea()

            if tasks.isEmpty {
                ScrollView {
                    EmptyStateView(
                        title: "No tasks here",
                        message: "This project has nothing filed yet.",
                        systemImage: "tray"
                    )
                    .padding(.top, Space.x10)
                }
                .refreshable { await store.refresh() }
            } else {
                List {
                    ForEach(tasks) { task in
                        NavigationLink { TaskDetailView(taskId: task.id) } label: {
                            TaskRow(task: task)
                        }
                        .listRowBackground(Theme.bgPage(scheme))
                    }
                }
                .listStyle(.plain)
                .refreshable { await store.refresh() }
            }
        }
        .navigationTitle(project.name)
        .navigationBarTitleDisplayMode(.inline)
    }
}

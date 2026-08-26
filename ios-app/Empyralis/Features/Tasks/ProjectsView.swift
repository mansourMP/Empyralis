import SwiftUI

struct ProjectsView: View {
    @EnvironmentObject private var store: WorkspaceStore
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.bgPage(scheme).ignoresSafeArea()

                if !store.hasLoadedOnce {
                    if let error = store.loadError {
                        // Nothing known AND the read failed. "Empty" and
                        // "couldn't load" are different facts — a permanent
                        // skeleton here was the collapse.
                        ScrollView {
                            EmptyStateView(
                                title: "Couldn't load projects",
                                message: error,
                                systemImage: "wifi.exclamationmark"
                            )
                            .padding(.top, Space.x10)
                        }
                        .refreshable { await store.refresh() }
                    } else {
                        // The ONLY state that may show a skeleton: nothing
                        // known yet, and no failure to report either.
                        List {
                            ForEach(0..<5, id: \.self) { _ in
                                SkeletonRow().listRowBackground(Theme.bgPage(scheme))
                            }
                        }
                        .listStyle(.plain)
                    }
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
                                ProjectDetailView(project: project)
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

/// A PROJECT IS TASKS AND DOCUMENTS — the web's own `PROJECT_TAB_VIEWS`,
/// which is exactly `["tasks", "documents"]` and nothing else.
///
/// Documents used to be a top-level tab on this app, which contradicted a
/// decision the web already made: a cross-project document LENS does not
/// earn a permanent rail slot, because documents belong to the project whose
/// knowledge they are. Moving them here is that decision applied, not a
/// reorganisation for its own sake — and it is what frees the fifth tab slot
/// for the "My work" surface the phone was missing entirely.
///
/// The picker is a plain segmented control: selection is weight and shape,
/// never hue, and there is no accent anywhere on this screen.
struct ProjectDetailView: View {
    let project: Project

    @Environment(\.colorScheme) private var scheme
    @State private var section: Section = .tasks
    @State private var showTaskComposer = false

    /// Mirrors PROJECT_TAB_VIEWS / PROJECT_TAB_LABEL. Deliberately not a
    /// third member — Agents were removed from a project's tab bar on the
    /// web (an agent belongs to the WORKSPACE), and this app already agrees
    /// by putting Agents at the top level.
    enum Section: String, CaseIterable, Identifiable {
        case tasks
        case documents

        var id: String { rawValue }

        var label: String {
            switch self {
            case .tasks: return "Tasks"
            case .documents: return "Documents"
            }
        }
    }

    var body: some View {
        ZStack {
            Theme.bgPage(scheme).ignoresSafeArea()

            VStack(spacing: 0) {
                Picker("Section", selection: $section) {
                    ForEach(Section.allCases) { value in
                        Text(value.label).tag(value)
                    }
                }
                .pickerStyle(.segmented)
                .padding(.horizontal, Space.x4)
                .padding(.vertical, Space.x2)

                switch section {
                case .tasks:
                    ProjectTasksSection(project: project)
                case .documents:
                    ProjectDocumentsView(project: project)
                }
            }
        }
        .navigationTitle(project.name)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            // Only on Tasks — creating a document isn't part of this
            // change, and a "+" that always creates a task while the
            // Documents segment is showing would be doing something the
            // screen in front of the person doesn't say.
            if section == .tasks {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showTaskComposer = true
                    } label: {
                        Image(systemName: "plus")
                    }
                    .accessibilityLabel("New task")
                }
            }
        }
        .sheet(isPresented: $showTaskComposer) {
            NewTaskSheet(projectId: project.id, parentTask: nil)
        }
    }
}

struct ProjectTasksSection: View {
    let project: Project
    @EnvironmentObject private var store: WorkspaceStore
    @Environment(\.colorScheme) private var scheme

    private var tasks: [EmpTask] {
        store.tasks(inProject: project.id)
    }

    var body: some View {
        Group {
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
    }
}

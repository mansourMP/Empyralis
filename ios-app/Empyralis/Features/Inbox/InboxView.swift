import SwiftUI
import UIKit

/// Reads from the local store, never from the network directly — so it is
/// populated on the first frame after launch. Pull-to-refresh triggers a
/// background sync; the list never blanks while that runs.
///
/// Read-only about agents, deliberately: this app never composes a message
/// to one. Talking to an agent happens in its channel (Telegram/Slack).
struct InboxView: View {
    /// Owned by MainTabView so a deep link (notification tap, or a task URL
    /// from a channel message) can push a destination into this stack from
    /// outside the view.
    @Binding var path: NavigationPath

    @EnvironmentObject private var store: WorkspaceStore
    @Environment(\.colorScheme) private var scheme

    private var openTasks: [EmpTask] {
        store.tasks.filter { !$0.isDone }
    }

    var body: some View {
        NavigationStack(path: $path) {
            ZStack {
                Theme.bgPage(scheme).ignoresSafeArea()
                content
            }
            .navigationTitle("Inbox")
            .navigationDestination(for: TaskRoute.self) { route in
                TaskDetailView(taskId: route.taskId)
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        if !store.hasLoadedOnce {
            // The ONLY state that may show a skeleton: nothing known yet.
            List {
                ForEach(0..<6, id: \.self) { _ in
                    SkeletonRow().listRowBackground(Theme.bgPage(scheme))
                }
            }
            .listStyle(.plain)
            .overlay(alignment: .center) {
                if let error = store.loadError {
                    EmptyStateView(
                        title: "Couldn't load",
                        message: error,
                        systemImage: "wifi.exclamationmark"
                    )
                    .background(Theme.bgPage(scheme))
                }
            }
        } else if openTasks.isEmpty {
            ScrollView {
                EmptyStateView(
                    title: "You're all caught up",
                    message: "Nothing open right now.",
                    systemImage: "checkmark.circle"
                )
                .padding(.top, Space.x10)
            }
            .refreshable { await store.refresh() }
        } else {
            List {
                ForEach(openTasks) { task in
                    // Value-based so taps and deep links push through the
                    // SAME path — two mechanisms would drift.
                    NavigationLink(value: TaskRoute(taskId: task.id)) {
                        TaskRow(task: task)
                    }
                    .listRowBackground(Theme.bgPage(scheme))
                    .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                        Button {
                            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                            Task { await store.setTaskStatus(task, to: "done") }
                        } label: {
                            Label("Done", systemImage: "checkmark")
                        }
                        .tint(Theme.taskStatus("done", scheme))
                    }
                }
            }
            .listStyle(.plain)
            .refreshable { await store.refresh() }
        }
    }
}

struct TaskRow: View {
    let task: EmpTask
    @Environment(\.colorScheme) private var scheme

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
                }
            }
        }
        .padding(.vertical, Space.x1)
    }
}

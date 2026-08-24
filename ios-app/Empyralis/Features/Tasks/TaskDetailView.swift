import SwiftUI
import UIKit

/// Takes a task ID rather than a task VALUE, and re-reads it from the store
/// on every render. That is what lets an optimistic status change, or a
/// background sync, update this screen live — a captured struct would show
/// a stale copy until the view was popped and pushed again.
struct TaskDetailView: View {
    let taskId: String

    @EnvironmentObject private var store: WorkspaceStore
    @Environment(\.colorScheme) private var scheme
    @State private var writeError: String?

    private var task: EmpTask? {
        store.tasks.first { $0.id == taskId }
    }

    var body: some View {
        ZStack {
            Theme.bgPage(scheme).ignoresSafeArea()

            if let task {
                ScrollView {
                    VStack(alignment: .leading, spacing: Space.x5) {
                        header(task)
                        statusPicker(task)
                        if let description = task.description, !description.isEmpty {
                            VStack(alignment: .leading, spacing: Space.x2) {
                                Text("Description")
                                    .font(.empSectionHeader)
                                    .foregroundStyle(Theme.textMuted(scheme))
                                Text(description)
                                    .font(.empBody)
                                    .foregroundStyle(Theme.textSecondary(scheme))
                            }
                        }
                    }
                    .padding(Space.x4)
                }
            } else {
                // The task genuinely isn't in the store — deleted elsewhere,
                // or filtered out. Say that; never render a blank screen.
                EmptyStateView(
                    title: "Task unavailable",
                    message: "It may have been deleted or moved.",
                    systemImage: "questionmark.circle"
                )
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .alert("Couldn't save", isPresented: .constant(writeError != nil)) {
            Button("OK") { writeError = nil }
        } message: {
            Text(writeError ?? "")
        }
    }

    private func header(_ task: EmpTask) -> some View {
        VStack(alignment: .leading, spacing: Space.x2) {
            if let displayId = task.displayId, !displayId.isEmpty {
                Text(displayId)
                    .font(.empMono)
                    .foregroundStyle(Theme.textMuted(scheme))
            }
            Text(task.title)
                .font(.empTitle)
                .foregroundStyle(Theme.textPrimary(scheme))
        }
    }

    /// Every status is a real, reachable transition — the backend's PATCH
    /// treats them as plain field updates with no gate — so none of these
    /// rows is a control that can't be used.
    private func statusPicker(_ task: EmpTask) -> some View {
        VStack(alignment: .leading, spacing: Space.x2) {
            Text("Status")
                .font(.empSectionHeader)
                .foregroundStyle(Theme.textMuted(scheme))

            VStack(spacing: 0) {
                ForEach(Array(TaskStatusOption.allCases.enumerated()), id: \.element) { index, option in
                    Button {
                        guard option.rawValue != task.status else { return }
                        UIImpactFeedbackGenerator(style: .light).impactOccurred()
                        Task {
                            if let error = await store.setTaskStatus(task, to: option.rawValue) {
                                writeError = error
                            }
                        }
                    } label: {
                        HStack(spacing: Space.x3) {
                            StatusDot(status: option.rawValue)
                            Text(option.label)
                                .font(option.rawValue == task.status ? .empBodyMedium : .empBody)
                                .foregroundStyle(Theme.textPrimary(scheme))
                            Spacer()
                            // Selection is WEIGHT and SHAPE, never hue —
                            // the accent law from the web app carries over.
                            if option.rawValue == task.status {
                                Image(systemName: "checkmark")
                                    .font(.system(size: 12, weight: .semibold))
                                    .foregroundStyle(Theme.textPrimary(scheme))
                            }
                        }
                        .padding(.horizontal, Space.x3)
                        .frame(height: 44)
                    }
                    .buttonStyle(.plain)

                    if index < TaskStatusOption.allCases.count - 1 {
                        Divider().overlay(Theme.border(scheme)).padding(.leading, Space.x3)
                    }
                }
            }
            .background(Theme.bgCard(scheme), in: RoundedRectangle(cornerRadius: Radius.card))
            .overlay(
                RoundedRectangle(cornerRadius: Radius.card)
                    .stroke(Theme.border(scheme), lineWidth: 1)
            )
        }
    }
}

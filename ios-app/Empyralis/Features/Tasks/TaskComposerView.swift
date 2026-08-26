import SwiftUI
import UIKit

/// Creates a task, or — when `parentTask` is set — a SUB-task of it. One
/// screen for both: a sub-task IS a task, and the only difference on the
/// wire is one extra field on the same create call (see
/// WorkspaceStore.createTask), so two near-identical forms would be the
/// "guard called once... the next branch will skip" shape one level up,
/// applied to a form instead of a guard.
///
/// TITLE ONLY, PLUS AN OPTIONAL DESCRIPTION — everything else on the web's
/// own composer (status/priority/assignee/labels/due) is reachable a beat
/// later on TaskDetailView, which already edits every one of those well.
/// Porting all six chips here would be the ceremony that composer's own
/// header comment warns against ("six always-visible chips read as an
/// application form") on a screen a third the width of where that composer
/// already made the same call once.
///
/// NOT OPTIMISTIC, unlike every other write in this app — see
/// WorkspaceStore.createTask's own header for why. This shows "Creating…"
/// and waits for a real, server-minted task, matching the web's own
/// TaskComposer (no fabricated placeholder row).
struct NewTaskSheet: View {
    let projectId: String
    /// nil = a top-level task. Set = a sub-task of this task, in the SAME
    /// project — the backend refuses a parent that belongs to a different
    /// project (`_resolve_parent_task`).
    let parentTask: EmpTask?

    @EnvironmentObject private var store: WorkspaceStore
    @Environment(\.colorScheme) private var scheme
    @Environment(\.dismiss) private var dismiss

    @State private var title = ""
    @State private var description = ""
    @State private var isCreating = false
    @State private var errorMessage: String?
    @FocusState private var titleFocused: Bool

    private var canCreate: Bool {
        TaskAuthoring.canCreateTask(title: title, isCreating: isCreating)
    }

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.bgPage(scheme).ignoresSafeArea()
                ScrollView {
                    VStack(alignment: .leading, spacing: Space.x4) {
                        if let parentTask {
                            HStack(spacing: Space.x2) {
                                Text("Sub-task of")
                                    .font(.empCaption)
                                    .foregroundStyle(Theme.textMuted(scheme))
                                Text(parentTask.title)
                                    .font(.empCaptionMedium)
                                    .foregroundStyle(Theme.textSecondary(scheme))
                                    .lineLimit(1)
                            }
                        }

                        TextField("Task title", text: $title, axis: .vertical)
                            .font(.empBodyMedium)
                            .foregroundStyle(Theme.textPrimary(scheme))
                            .lineLimit(1...4)
                            .focused($titleFocused)
                            .padding(Space.x3)
                            .background(Theme.bgField(scheme), in: RoundedRectangle(cornerRadius: Radius.control))
                            .overlay(
                                RoundedRectangle(cornerRadius: Radius.control)
                                    .stroke(titleFocused ? Theme.borderStrong(scheme) : Theme.border(scheme), lineWidth: 1)
                            )

                        descriptionField

                        if let errorMessage {
                            Text(errorMessage)
                                .font(.empCaption)
                                .foregroundStyle(Theme.offline(scheme))
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .padding(Space.x4)
                    .padding(.bottom, Space.x10)
                }
                .scrollDismissesKeyboard(.interactively)
                .safeAreaInset(edge: .bottom) { createButton }
            }
            .navigationTitle(parentTask == nil ? "New task" : "New sub-task")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { dismiss() }
                        .font(.empBody)
                        .foregroundStyle(Theme.textSecondary(scheme))
                }
            }
            .onAppear { titleFocused = true }
        }
    }

    private var descriptionField: some View {
        ZStack(alignment: .topLeading) {
            if description.isEmpty {
                Text("Description (optional)")
                    .font(.empBody)
                    .foregroundStyle(Theme.textMuted(scheme))
                    .padding(.horizontal, Space.x3)
                    .padding(.vertical, Space.x3 + 4)
                    .allowsHitTesting(false)
            }
            TextEditor(text: $description)
                .font(.empBody)
                .foregroundStyle(Theme.textPrimary(scheme))
                .scrollContentBackground(.hidden)
                .frame(minHeight: 120)
                .padding(Space.x2)
        }
        .background(Theme.bgField(scheme), in: RoundedRectangle(cornerRadius: Radius.control))
        .overlay(
            RoundedRectangle(cornerRadius: Radius.control)
                .stroke(Theme.border(scheme), lineWidth: 1)
        )
    }

    /// THE ONE PRIMARY ACTION ON THIS SCREEN, and therefore the only place
    /// `Theme.accent` (via PrimaryButtonStyle) appears — Cancel above is
    /// plain text, matching every other sheet's Cancel in this app.
    private var createButton: some View {
        Button {
            create()
        } label: {
            Text(isCreating ? "Creating…" : "Create task")
        }
        .buttonStyle(PrimaryButtonStyle())
        .disabled(!canCreate)
        .padding(.horizontal, Space.x4)
        .padding(.vertical, Space.x3)
        .background(.bar)
    }

    private func create() {
        guard canCreate else { return }
        isCreating = true
        errorMessage = nil
        Task {
            let outcome = await store.createTask(
                projectId: projectId,
                title: title,
                description: description,
                parentTaskId: parentTask?.id
            )
            isCreating = false
            switch outcome {
            case .created, .createdUnconfirmed:
                // Both mean the write landed — see TaskCreateOutcome's own
                // header for why an unconfirmed reply is not a failure.
                UIImpactFeedbackGenerator(style: .light).impactOccurred()
                dismiss()
            case .failed(let message):
                // Keep the sheet open with the draft intact so the person
                // can retry — the same posture the web's own composer takes
                // on a failed create.
                errorMessage = message
            }
        }
    }
}

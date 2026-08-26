import SwiftUI
import UIKit

/// Takes a task ID rather than a task VALUE, and re-reads it from the store
/// on every render. That is what lets an optimistic write, or a background
/// sync, update this screen live — a captured struct would show a stale copy
/// until the view was popped and pushed again.
///
/// ── WHAT THIS SCREEN CAN AND CANNOT DO, AND WHY ─────────────────────────
/// Parity target is the web's TaskDetailView.tsx, collapsed from its two
/// columns into one for a phone. Everything it renders is backed by a real
/// route in routes_fleet.py, and everything the backend cannot do is either
/// absent or displayed read-only — never a control that submits nothing.
///
///   Status      PATCH  .../tasks/{id}          all seven, plain field update
///   Priority    PATCH  .../tasks/{id}          0-4, and 0 is a real choice
///   Due date    PATCH  .../tasks/{id}          `clear_due_at` clears it
///   Assignee    POST   .../tasks/{id}/assign   agent_id XOR user_id
///   Labels      POST/DELETE .../labels         attach by id, detach by id
///   Comment     POST   .../tasks/{id}/comments the one create on this screen
///   Sub-tasks   derived from the store — a sub-task is a plain row in the
///               same table and the list route is not top-level-filtered, so
///               they are already in hand and no round trip is needed
///
/// NOT OFFERED, because the backend has no way to do it: UNASSIGNING.
/// `fleet_assign_task` refuses a body with neither id ("agent_id or user_id
/// is required to assign a task") and the PATCH route explicitly does not
/// carry an assignee, so there is no request this app could send. The
/// picker therefore has no "Unassigned" row — an unassigned task still
/// DISPLAYS as unassigned, it simply cannot be returned to that state here.
struct TaskDetailView: View {
    let taskId: String

    @EnvironmentObject private var store: WorkspaceStore
    @EnvironmentObject private var session: SessionStore
    @Environment(\.colorScheme) private var scheme

    @State private var writeError: String?
    @State private var activeSheet: DetailSheet?
    @State private var commentDraft: String = ""
    @State private var isPostingComment = false
    @FocusState private var composerFocused: Bool

    private enum DetailSheet: String, Identifiable {
        case status, priority, assignee, due, labels, title, description
        var id: String { rawValue }
    }

    private var task: EmpTask? { store.task(taskId) }

    var body: some View {
        ZStack {
            Theme.bgPage(scheme).ignoresSafeArea()

            if let task {
                ScrollView {
                    VStack(alignment: .leading, spacing: Space.x5) {
                        header(task)
                        pendingWakeNotice(task)
                        propertiesCard(task)
                        descriptionSection(task)
                        subtasksSection(task)
                        activitySection(task)
                        attributionSection(task)
                    }
                    .padding(Space.x4)
                    .padding(.bottom, Space.x6)
                }
                .scrollDismissesKeyboard(.interactively)
                .safeAreaInset(edge: .bottom) { composer(task) }
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
        .sheet(item: $activeSheet) { sheet in
            if let task { sheetContent(sheet, task) }
        }
        .alert("Couldn't save", isPresented: .constant(writeError != nil)) {
            Button("OK") { writeError = nil }
        } message: {
            Text(writeError ?? "")
        }
    }

    // MARK: - Header

    /// Tappable, like every other editable field on this screen — a pencil
    /// glyph rather than PropertyRow's chevron, because this opens an edit
    /// sheet for the title ITSELF rather than navigating to something else.
    private func header(_ task: EmpTask) -> some View {
        Button {
            activeSheet = .title
        } label: {
            HStack(alignment: .top, spacing: Space.x2) {
                VStack(alignment: .leading, spacing: Space.x2) {
                    if let displayId = task.displayId, !displayId.isEmpty {
                        Text(displayId)
                            .font(.empMono)
                            .foregroundStyle(Theme.textMuted(scheme))
                    }
                    Text(task.title)
                        .font(.empTitle)
                        .foregroundStyle(Theme.textPrimary(scheme))
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                }
                Spacer(minLength: Space.x2)
                Image(systemName: "pencil")
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(Theme.textMuted(scheme))
                    .padding(.top, 4)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    /// MAN-294's honesty row. A task can read "In progress" while the wake
    /// behind that claim is still sitting in the scheduler — which is exactly
    /// "assigned, but nobody has actually started". Rendered only when the
    /// backend says there IS a pending wake, so it never speculates.
    @ViewBuilder
    private func pendingWakeNotice(_ task: EmpTask) -> some View {
        if task.hasPendingWake {
            HStack(alignment: .top, spacing: Space.x2) {
                Image(systemName: "clock")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Theme.warning(scheme))
                VStack(alignment: .leading, spacing: 2) {
                    Text("Waiting to start")
                        .font(.empCaptionMedium)
                        .foregroundStyle(Theme.textPrimary(scheme))
                    Text(pendingWakeDetail(task))
                        .font(.empCaption)
                        .foregroundStyle(Theme.textSecondary(scheme))
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(Space.x3)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.bgInset(scheme), in: RoundedRectangle(cornerRadius: Radius.card))
        }
    }

    private func pendingWakeDetail(_ task: EmpTask) -> String {
        let when = TaskDates.timeAgo(task.pendingWakeDueAt)
        if let reason = task.pendingWakeDelayReason, !reason.isEmpty {
            let readable = reason.replacingOccurrences(of: "_", with: " ")
            return when.isEmpty
                ? "The assignee hasn't picked this up yet — \(readable)."
                : "The assignee hasn't picked this up yet — \(readable). Due \(when)."
        }
        return when.isEmpty
            ? "This is assigned, but the assignee hasn't picked it up yet."
            : "This is assigned, but the assignee hasn't picked it up yet. Due \(when)."
    }

    // MARK: - Properties

    private func propertiesCard(_ task: EmpTask) -> some View {
        DetailCard {
            PropertyRow(title: "Status", action: { activeSheet = .status }) {
                HStack(spacing: Space.x2) {
                    StatusDot(status: task.status)
                    Text(task.statusLabel)
                        .font(.empBody)
                        .foregroundStyle(Theme.textPrimary(scheme))
                }
            }
            RowDivider()
            PropertyRow(title: "Priority", action: { activeSheet = .priority }) {
                HStack(spacing: Space.x2) {
                    PriorityGlyph(priority: task.priorityValue)
                    Text(task.priorityValue.label)
                        .font(.empBody)
                        .foregroundStyle(task.priorityValue == .none
                                         ? Theme.textMuted(scheme)
                                         : Theme.textPrimary(scheme))
                }
            }
            RowDivider()
            assigneeRow(task)
            RowDivider()
            PropertyRow(title: "Due", action: { activeSheet = .due }) {
                dueValue(task)
            }
            RowDivider()
            labelsRow(task)
        }
    }

    @ViewBuilder
    private func dueValue(_ task: EmpTask) -> some View {
        if let due = TaskDates.due(task.dueAt) {
            let overdue = TaskDates.isOverdue(task.dueAt) && !task.isDone
            Text(due)
                .font(.empBody)
                .foregroundStyle(overdue ? Theme.warning(scheme) : Theme.textPrimary(scheme))
        } else {
            Text("No due date")
                .font(.empBody)
                .foregroundStyle(Theme.textMuted(scheme))
        }
    }

    /// Assignable = this workspace's real agents plus its members. When BOTH
    /// are unavailable the row stops being a button rather than opening an
    /// empty picker, and when identity failed to LOAD it says so instead of
    /// claiming the task is unassigned — those are different facts.
    @ViewBuilder
    private func assigneeRow(_ task: EmpTask) -> some View {
        let canAssign = !store.realAgents.isEmpty || !store.members.isEmpty
        PropertyRow(
            title: "Assignee",
            isInteractive: canAssign,
            action: canAssign ? { activeSheet = .assignee } : nil
        ) {
            if let actor = resolvedAssignee(task) {
                HStack(spacing: Space.x2) {
                    ActorAvatar(actor: actor, size: 22)
                    Text(actor.name)
                        .font(.empBody)
                        .foregroundStyle(actor == .lookupFailed
                                         ? Theme.textMuted(scheme)
                                         : Theme.textPrimary(scheme))
                        // 2 lines, not 1 — this is a real agent/member name,
                        // not fixed vocabulary. PropertyRow's own row is
                        // minHeight (not fixed), so a wrapped second line
                        // never clips; it just gives a long name somewhere to
                        // go instead of an ellipsis at large accessibility
                        // sizes.
                        .lineLimit(2)
                }
            } else if store.identityLookupFailed && !canAssign {
                Text("Couldn't load who")
                    .font(.empBody)
                    .foregroundStyle(Theme.textMuted(scheme))
            } else {
                Text("Unassigned")
                    .font(.empBody)
                    .foregroundStyle(Theme.textMuted(scheme))
            }
        }
    }

    private func resolvedAssignee(_ task: EmpTask) -> ResolvedActor? {
        ActorResolver.resolve(
            id: task.assigneeAgentId ?? task.assigneeUserId,
            agents: store.agents,
            members: store.members,
            identityLookupFailed: store.identityLookupFailed
        )
    }

    /// The vocabulary is curated server-side — `attach_label` refuses a name
    /// it does not already know rather than minting it — so with an empty
    /// vocabulary there is genuinely nothing to attach and the row renders
    /// its existing chips read-only instead of opening a picker that could
    /// only fail. Same rule the web states for its own Labels row.
    @ViewBuilder
    private func labelsRow(_ task: EmpTask) -> some View {
        let canEdit = !store.labelVocabulary.isEmpty
        PropertyRow(
            title: "Labels",
            isInteractive: canEdit,
            action: canEdit ? { activeSheet = .labels } : nil
        ) {
            if task.labels.isEmpty {
                Text(canEdit ? "None" : "No labels")
                    .font(.empBody)
                    .foregroundStyle(Theme.textMuted(scheme))
            } else {
                // Capped at two chips plus a count. The value slot on an
                // iPhone 13 is ~250pt after the 88pt title and the chevron,
                // so a wrapping row of chips is a real clipping risk on the
                // narrowest screen this app supports — and the full set is
                // one tap away in the sheet, where it has the room. "+2" is
                // a true statement about what is not shown, which a silently
                // truncated row would not be.
                HStack(spacing: Space.x2) {
                    ForEach(task.labels.prefix(2)) { label in
                        LabelChip(label: label)
                    }
                    if task.labels.count > 2 {
                        Text("+\(task.labels.count - 2)")
                            .font(.empCaption)
                            .foregroundStyle(Theme.textMuted(scheme))
                    }
                }
            }
        }
    }

    // MARK: - Description

    /// ALWAYS renders, unlike the version this replaced — which rendered
    /// NOTHING at all when a task had no description, so there was no way
    /// to add one from this screen: the section itself was the dead end,
    /// not just a missing control on it. Tappable either way, with an
    /// honest "Add a description…" placeholder standing in for empty text.
    private func descriptionSection(_ task: EmpTask) -> some View {
        let text = task.description ?? ""
        return VStack(alignment: .leading, spacing: Space.x2) {
            SectionHeader(title: "Description")
            Button {
                activeSheet = .description
            } label: {
                HStack(alignment: .top, spacing: Space.x2) {
                    Text(text.isEmpty ? "Add a description…" : text)
                        .font(.empBody)
                        .foregroundStyle(text.isEmpty ? Theme.textMuted(scheme) : Theme.textSecondary(scheme))
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Image(systemName: "pencil")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(Theme.textMuted(scheme))
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
    }

    // MARK: - Sub-tasks

    @ViewBuilder
    private func subtasksSection(_ task: EmpTask) -> some View {
        let children = store.subtasks(of: task.id)
        if task.subtaskCount > 0 || !children.isEmpty {
            VStack(alignment: .leading, spacing: Space.x2) {
                SectionHeader(
                    title: "Sub-tasks",
                    trailing: "\(task.subtaskDoneCount)/\(task.subtaskCount)"
                )
                DetailCard {
                    ForEach(Array(children.enumerated()), id: \.element.id) { index, child in
                        NavigationLink {
                            TaskDetailView(taskId: child.id)
                        } label: {
                            subtaskRow(child)
                        }
                        .buttonStyle(.plain)
                        if index < children.count - 1 { RowDivider() }
                    }
                    // The parent's rollup is authoritative; this list is only
                    // what has synced. Saying so beats showing a short list
                    // as if it were the whole set.
                    if children.count < task.subtaskCount {
                        if !children.isEmpty { RowDivider() }
                        Text(children.isEmpty
                             ? "\(task.subtaskCount) sub-task\(task.subtaskCount == 1 ? "" : "s") haven't synced yet. Pull to refresh."
                             : "\(task.subtaskCount - children.count) more haven't synced yet. Pull to refresh.")
                            .font(.empCaption)
                            .foregroundStyle(Theme.textMuted(scheme))
                            .padding(.horizontal, Space.x3)
                            .padding(.vertical, Space.x3)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
        }
    }

    private func subtaskRow(_ child: EmpTask) -> some View {
        HStack(spacing: Space.x3) {
            StatusDot(status: child.status)
            VStack(alignment: .leading, spacing: 2) {
                Text(child.title)
                    .font(.empBody)
                    .foregroundStyle(Theme.textPrimary(scheme))
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                HStack(spacing: Space.x2) {
                    if let displayId = child.displayId {
                        Text(displayId)
                            .font(.empMono)
                            .foregroundStyle(Theme.textMuted(scheme))
                    }
                    Text(child.statusLabel)
                        .font(.empCaption)
                        .foregroundStyle(Theme.textMuted(scheme))
                }
            }
            Spacer()
            Image(systemName: "chevron.right")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(Theme.textMuted(scheme).opacity(0.7))
        }
        .padding(.horizontal, Space.x3)
        .padding(.vertical, Space.x3)
        .contentShape(Rectangle())
    }

    // MARK: - Activity

    /// Comments and structured activity events are ONE feed, merged and
    /// ordered oldest-first so the newest sits against the composer — a
    /// comment thread reads downward. Both come off `task.metadata`; there is
    /// no comments endpoint to GET and no second fetch here.
    private func feedItems(_ task: EmpTask) -> [FeedItem] {
        var items: [FeedItem] = task.metadata.comments.map { .comment($0) }
        items += task.metadata.activity.map { .activity($0) }
        return items.sorted { lhs, rhs in
            let l = TaskDates.parse(lhs.timestamp) ?? .distantPast
            let r = TaskDates.parse(rhs.timestamp) ?? .distantPast
            return l < r
        }
    }

    private enum FeedItem: Identifiable {
        case comment(TaskComment)
        case activity(TaskActivityEvent)

        var id: String {
            switch self {
            case .comment(let c): return "c_\(c.id)"
            case .activity(let a): return "a_\(a.type)_\(a.timestamp)_\(a.actorId ?? "")"
            }
        }

        var timestamp: String {
            switch self {
            case .comment(let c): return c.createdAt
            case .activity(let a): return a.timestamp
            }
        }
    }

    @ViewBuilder
    private func activitySection(_ task: EmpTask) -> some View {
        let items = feedItems(task)
        VStack(alignment: .leading, spacing: Space.x3) {
            SectionHeader(
                title: "Activity",
                trailing: task.metadata.comments.isEmpty
                    ? nil
                    : "\(task.metadata.comments.count) comment\(task.metadata.comments.count == 1 ? "" : "s")"
            )
            if items.isEmpty {
                Text("Nothing yet. A comment here reaches the assignee on their next turn.")
                    .font(.empSecondary)
                    .foregroundStyle(Theme.textMuted(scheme))
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                VStack(alignment: .leading, spacing: Space.x4) {
                    ForEach(items) { item in
                        switch item {
                        case .comment(let comment): commentRow(comment)
                        case .activity(let event): activityRow(event)
                        }
                    }
                }
            }
        }
    }

    private func commentRow(_ comment: TaskComment) -> some View {
        let actor = ActorResolver.resolve(
            id: comment.authorId,
            agents: store.agents,
            members: store.members,
            identityLookupFailed: store.identityLookupFailed,
            displayNameSnapshot: comment.authorDisplayName
        )
        return HStack(alignment: .top, spacing: Space.x3) {
            ActorAvatar(actor: actor ?? .unknown(shortId: "?"), size: 26)
            VStack(alignment: .leading, spacing: Space.x1) {
                HStack(spacing: Space.x2) {
                    Text(actor?.name ?? "Unknown")
                        .font(.empCaptionMedium)
                        .foregroundStyle(Theme.textPrimary(scheme))
                    // "Sending" is a real, separate state from "sent" — the
                    // optimistic copy says so rather than looking identical
                    // to a comment the server has actually accepted.
                    Text(comment.isPending ? "Sending…" : TaskDates.timeAgo(comment.createdAt))
                        .font(.empCaption)
                        .foregroundStyle(Theme.textMuted(scheme))
                }
                Text(comment.body)
                    .font(.empBody)
                    .foregroundStyle(Theme.textSecondary(scheme))
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .opacity(comment.isPending ? 0.6 : 1)
    }

    private func activityRow(_ event: TaskActivityEvent) -> some View {
        let actor = ActorResolver.resolve(
            id: event.actorId,
            agents: store.agents,
            members: store.members,
            identityLookupFailed: store.identityLookupFailed,
            displayNameSnapshot: event.actorName
        )
        return HStack(alignment: .top, spacing: Space.x3) {
            Circle()
                .fill(Theme.textMuted(scheme).opacity(0.35))
                .frame(width: 6, height: 6)
                .padding(.leading, 10)
                .padding(.top, 6)
            (
                Text(actor?.name ?? "Someone").font(.empCaptionMedium)
                + Text(" \(event.summary)").font(.empCaption)
                + Text(" · \(TaskDates.timeAgo(event.timestamp))").font(.empCaption)
            )
            .foregroundStyle(Theme.textMuted(scheme))
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    // MARK: - Attribution

    /// "Created by" / "Completed by", the same real identities the Assignee
    /// row resolves. Rendered only when there is someone to name.
    @ViewBuilder
    private func attributionSection(_ task: EmpTask) -> some View {
        let creator = ActorResolver.resolve(
            id: task.createdBy, agents: store.agents, members: store.members,
            identityLookupFailed: store.identityLookupFailed
        )
        let completer = ActorResolver.resolve(
            id: task.completedByUserId ?? task.completedByAgentId,
            agents: store.agents, members: store.members,
            identityLookupFailed: store.identityLookupFailed
        )
        if creator != nil || completer != nil {
            VStack(alignment: .leading, spacing: Space.x2) {
                SectionHeader(title: "Details")
                DetailCard {
                    if let creator {
                        PropertyRow(title: "Created by", isInteractive: false) {
                            actorLine(creator, stamp: task.createdAt)
                        }
                    }
                    if let completer {
                        if creator != nil { RowDivider() }
                        PropertyRow(title: "Completed by", isInteractive: false) {
                            actorLine(completer, stamp: task.completedAt)
                        }
                    }
                }
            }
        }
    }

    private func actorLine(_ actor: ResolvedActor, stamp: String?) -> some View {
        HStack(spacing: Space.x2) {
            ActorAvatar(actor: actor, size: 20)
            Text(actor.name)
                .font(.empSecondary)
                .foregroundStyle(Theme.textPrimary(scheme))
                // 2 lines — see the identical note on assigneeRow above.
                .lineLimit(2)
            if let when = stamp, !when.isEmpty {
                Text("· \(TaskDates.timeAgo(when))")
                    .font(.empCaption)
                    .foregroundStyle(Theme.textMuted(scheme))
            }
        }
    }

    // MARK: - Composer

    /// THE ONE PRIMARY ACTION ON THIS SCREEN, and therefore the only place
    /// `Theme.accent` appears. Every picker above selects with weight and a
    /// checkmark; nothing else here is tinted.
    private func composer(_ task: EmpTask) -> some View {
        let trimmed = commentDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        let canSend = !trimmed.isEmpty && !isPostingComment

        return HStack(alignment: .bottom, spacing: Space.x2) {
            TextField("Comment", text: $commentDraft, axis: .vertical)
                .font(.empBody)
                .foregroundStyle(Theme.textPrimary(scheme))
                .lineLimit(1...5)
                .focused($composerFocused)
                .padding(.horizontal, Space.x3)
                .padding(.vertical, 10)
                .background(Theme.bgInset(scheme), in: RoundedRectangle(cornerRadius: Radius.control))
                .overlay(
                    RoundedRectangle(cornerRadius: Radius.control)
                        .stroke(composerFocused ? Theme.borderStrong(scheme) : Theme.border(scheme), lineWidth: 1)
                )

            Button {
                send(task, body: trimmed)
            } label: {
                Image(systemName: isPostingComment ? "ellipsis" : "arrow.up")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(Theme.accentContrast)
                    .frame(width: 36, height: 36)
                    // THE ONE PRIMARY BUTTON ON THIS SCREEN (see the doc
                    // comment above) had no accessibility label at all — an
                    // icon-only Button synthesizes nothing from an SF Symbol
                    // by name alone in a way VoiceOver users can act on, so
                    // this read as "arrow up, button" with no indication it
                    // sends the comment. The label tracks the same state the
                    // glyph already does, so a VoiceOver user hears "Sending"
                    // rather than a stale "Send comment" while it's in flight.
                    .accessibilityLabel(isPostingComment ? "Sending" : "Send comment")
                    .background(
                        Circle().fill(canSend
                                      ? Theme.accent(scheme)
                                      : Theme.textMuted(scheme).opacity(0.35))
                    )
            }
            .buttonStyle(.plain)
            .disabled(!canSend)
            .animation(.easeOut(duration: 0.12), value: canSend)
        }
        .padding(.horizontal, Space.x4)
        .padding(.vertical, Space.x3)
        .background(.bar)
    }

    private func send(_ task: EmpTask, body: String) {
        guard !body.isEmpty, !isPostingComment else { return }
        isPostingComment = true
        commentDraft = ""
        composerFocused = false
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
        Task {
            let error = await store.postComment(task, body: body, authorUserId: session.user?.id)
            isPostingComment = false
            if let error {
                // Hand the text back rather than losing it — the person typed
                // it, and a failed send that also eats the message is two
                // losses for one failure.
                commentDraft = body
                writeError = error
            }
        }
    }

    // MARK: - Sheets

    @ViewBuilder
    private func sheetContent(_ sheet: DetailSheet, _ task: EmpTask) -> some View {
        switch sheet {
        case .status: statusSheet(task)
        case .priority: prioritySheet(task)
        case .assignee: assigneeSheet(task)
        case .due: dueSheet(task)
        case .labels: labelsSheet(task)
        case .title: titleSheet(task)
        case .description: descriptionEditSheet(task)
        }
    }

    /// Same shape as `apply(current:_:)` above (dismiss immediately, write
    /// async, surface a refusal through the shared alert) but text-entry
    /// sheets need the FINAL STRING the person typed, not a fixed picker
    /// value — so this is its own small helper rather than a sixth branch
    /// squeezed into `apply`'s `Bool`-only signature.
    private func titleSheet(_ task: EmpTask) -> some View {
        TextEditSheet(
            navTitle: "Edit title",
            placeholder: "Task title",
            initialText: task.title,
            requiresNonEmpty: true,
            multiline: false,
            isPresented: sheetBinding
        ) { newValue in
            activeSheet = nil
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
            Task {
                if let error = await store.setTaskTitle(task, to: newValue) { writeError = error }
            }
        }
    }

    private func descriptionEditSheet(_ task: EmpTask) -> some View {
        TextEditSheet(
            navTitle: "Edit description",
            placeholder: "Add a description…",
            initialText: task.description ?? "",
            requiresNonEmpty: false,
            multiline: true,
            isPresented: sheetBinding
        ) { newValue in
            activeSheet = nil
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
            Task {
                if let error = await store.setTaskDescription(task, to: newValue) { writeError = error }
            }
        }
    }

    private func statusSheet(_ task: EmpTask) -> some View {
        PickerSheet(title: "Status", isPresented: sheetBinding) {
            ForEach(TaskStatusOption.allCases) { option in
                PickerRow(
                    title: option.label,
                    isSelected: option.rawValue == task.normalizedStatus,
                    action: {
                        apply(current: option.rawValue == task.normalizedStatus) {
                            await store.setTaskStatus(task, to: option.rawValue)
                        }
                    }
                ) {
                    StatusDot(status: option.rawValue)
                }
            }
        }
        .presentationDetents([.medium, .large])
    }

    private func prioritySheet(_ task: EmpTask) -> some View {
        PickerSheet(title: "Priority", isPresented: sheetBinding) {
            ForEach(TaskPriority.pickerOrder) { level in
                PickerRow(
                    title: level.label,
                    isSelected: level.rawValue == task.priority,
                    action: {
                        apply(current: level.rawValue == task.priority) {
                            await store.setTaskPriority(task, to: level)
                        }
                    }
                ) {
                    PriorityGlyph(priority: level)
                }
            }
        }
        .presentationDetents([.medium])
    }

    /// Agents and people in one list, separated by a header each. No
    /// "Unassigned" row — see this file's header for why that is a backend
    /// fact rather than an omission.
    private func assigneeSheet(_ task: EmpTask) -> some View {
        PickerSheet(title: "Assignee", isPresented: sheetBinding) {
            if !store.realAgents.isEmpty {
                sheetGroupHeader("Agents")
                ForEach(store.realAgents) { agent in
                    PickerRow(
                        title: agent.displayName,
                        isSelected: agent.id == task.assigneeAgentId,
                        action: {
                            apply(current: agent.id == task.assigneeAgentId) {
                                await store.assignTask(task, toAgent: agent.id)
                            }
                        }
                    ) {
                        ActorAvatar(actor: .agent(name: agent.displayName), size: 22)
                    }
                }
            }
            if !store.members.isEmpty {
                sheetGroupHeader("People")
                ForEach(store.members) { member in
                    PickerRow(
                        title: member.name,
                        isSelected: member.userId == task.assigneeUserId,
                        action: {
                            apply(current: member.userId == task.assigneeUserId) {
                                await store.assignTask(task, toMember: member.userId)
                            }
                        }
                    ) {
                        ActorAvatar(actor: .person(name: member.name, initial: member.initial), size: 22)
                    }
                }
            }
            // The web degrades the same way when its member lookup is absent:
            // offer agents only, and say why rather than silently showing a
            // shorter list.
            if store.members.isEmpty && store.identityLookupFailed {
                Text("Couldn't load the people in this workspace, so only agents are listed. Pull to refresh on the task list.")
                    .font(.empCaption)
                    .foregroundStyle(Theme.textMuted(scheme))
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, Space.x4)
                    .padding(.top, Space.x3)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .presentationDetents([.medium, .large])
    }

    private func dueSheet(_ task: EmpTask) -> some View {
        DueDateSheet(
            current: TaskDates.parse(task.dueAt),
            isPresented: sheetBinding,
            onPick: { date in
                Task {
                    UIImpactFeedbackGenerator(style: .light).impactOccurred()
                    if let error = await store.setTaskDueDate(task, to: TaskDates.isoDateOnly(date)) {
                        writeError = error
                    }
                }
                activeSheet = nil
            },
            onClear: {
                Task {
                    UIImpactFeedbackGenerator(style: .light).impactOccurred()
                    if let error = await store.setTaskDueDate(task, to: nil) {
                        writeError = error
                    }
                }
                activeSheet = nil
            }
        )
    }

    private func labelsSheet(_ task: EmpTask) -> some View {
        PickerSheet(title: "Labels", isPresented: sheetBinding) {
            ForEach(store.labelVocabulary) { label in
                let attached = task.labels.contains { $0.id == label.id }
                PickerRow(
                    title: label.name,
                    isSelected: attached,
                    action: {
                        UIImpactFeedbackGenerator(style: .light).impactOccurred()
                        Task {
                            let error = attached
                                ? await store.detachLabel(task, label: label)
                                : await store.attachLabel(task, label: label)
                            if let error { writeError = error }
                        }
                    }
                ) {
                    Circle()
                        .fill(LabelPalette.color(label.colorToken))
                        .frame(width: 10, height: 10)
                        .frame(width: 16)
                }
            }
        }
        .presentationDetents([.medium, .large])
    }

    private func sheetGroupHeader(_ title: String) -> some View {
        Text(title.uppercased())
            .font(.empSectionHeader)
            .foregroundStyle(Theme.textMuted(scheme))
            .tracking(0.4)
            .padding(.horizontal, Space.x4)
            .padding(.top, Space.x4)
            .padding(.bottom, Space.x1)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var sheetBinding: Binding<Bool> {
        Binding(get: { activeSheet != nil }, set: { if !$0 { activeSheet = nil } })
    }

    /// Every picker tap funnels through here so the haptic, the dismissal and
    /// the error surfacing are identical across all five — and so a re-tap of
    /// the value that is ALREADY set sends no request at all.
    private func apply(current: Bool, _ write: @escaping () async -> String?) {
        guard !current else { activeSheet = nil; return }
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
        activeSheet = nil
        Task {
            if let error = await write() { writeError = error }
        }
    }
}

// MARK: - Due date sheet

/// Split out because it is the one picker that is not a list of rows. "Clear"
/// is rendered only when there IS a date to clear — the backend distinguishes
/// `clear_due_at: true` from an omitted field, and a Clear button on a task
/// with no due date would be a control with nothing to do.
private struct DueDateSheet: View {
    let current: Date?
    @Binding var isPresented: Bool
    let onPick: (Date) -> Void
    let onClear: () -> Void

    @State private var draft: Date
    @Environment(\.colorScheme) private var scheme

    init(current: Date?, isPresented: Binding<Bool>, onPick: @escaping (Date) -> Void, onClear: @escaping () -> Void) {
        self.current = current
        self._isPresented = isPresented
        self.onPick = onPick
        self.onClear = onClear
        self._draft = State(initialValue: current ?? Date())
    }

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.bgPage(scheme).ignoresSafeArea()
                VStack(spacing: Space.x4) {
                    DatePicker("Due date", selection: $draft, displayedComponents: .date)
                        .datePickerStyle(.graphical)
                        .padding(.horizontal, Space.x2)

                    if current != nil {
                        Button("Clear due date", action: onClear)
                            .font(.empBody)
                            .foregroundStyle(Theme.offline(scheme))
                    }
                    Spacer()
                }
                .padding(.top, Space.x2)
            }
            .navigationTitle("Due date")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { isPresented = false }
                        .font(.empBody)
                        .foregroundStyle(Theme.textSecondary(scheme))
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Set") { onPick(draft) }
                        .font(.empBodyMedium)
                        .foregroundStyle(Theme.textPrimary(scheme))
                }
            }
        }
        .presentationDetents([.large])
    }
}

// MARK: - Title / description edit sheet

/// A single free-text field, edited full-screen with Cancel/Save — the same
/// chrome DueDateSheet uses above, extended to text entry. Shared by the
/// task title and description edits, which differ only in whether an empty
/// result is a valid save (see TaskAuthoring.canSaveEdit) and whether the
/// field wraps to one line or many.
///
/// THERE IS NO FOCUS RING in this product — the border stepping from
/// `border` to `borderStrong` on focus, plus the caret, is the whole
/// signal, matching the composer field this screen already draws the same
/// way at its own bottom.
private struct TextEditSheet: View {
    let navTitle: String
    let placeholder: String
    let initialText: String
    let requiresNonEmpty: Bool
    let multiline: Bool
    @Binding var isPresented: Bool
    let onSave: (String) -> Void

    @State private var draft: String
    @FocusState private var focused: Bool
    @Environment(\.colorScheme) private var scheme

    init(
        navTitle: String,
        placeholder: String,
        initialText: String,
        requiresNonEmpty: Bool,
        multiline: Bool,
        isPresented: Binding<Bool>,
        onSave: @escaping (String) -> Void
    ) {
        self.navTitle = navTitle
        self.placeholder = placeholder
        self.initialText = initialText
        self.requiresNonEmpty = requiresNonEmpty
        self.multiline = multiline
        self._isPresented = isPresented
        self.onSave = onSave
        self._draft = State(initialValue: initialText)
    }

    private var trimmed: String { draft.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var canSave: Bool {
        TaskAuthoring.canSaveEdit(draft: draft, initial: initialText, requiresNonEmpty: requiresNonEmpty)
    }

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.bgPage(scheme).ignoresSafeArea()
                ScrollView {
                    field.padding(Space.x4)
                }
                .scrollDismissesKeyboard(.interactively)
            }
            .navigationTitle(navTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { isPresented = false }
                        .font(.empBody)
                        .foregroundStyle(Theme.textSecondary(scheme))
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Save") { onSave(trimmed) }
                        .font(.empBodyMedium)
                        .foregroundStyle(canSave ? Theme.textPrimary(scheme) : Theme.textMuted(scheme))
                        .disabled(!canSave)
                }
            }
        }
        .presentationDetents(multiline ? [.large] : [.medium, .large])
        .onAppear { focused = true }
    }

    @ViewBuilder
    private var field: some View {
        if multiline {
            ZStack(alignment: .topLeading) {
                if draft.isEmpty {
                    Text(placeholder)
                        .font(.empBody)
                        .foregroundStyle(Theme.textMuted(scheme))
                        .padding(.horizontal, Space.x3)
                        .padding(.vertical, Space.x3 + 4)
                        .allowsHitTesting(false)
                }
                TextEditor(text: $draft)
                    .font(.empBody)
                    .foregroundStyle(Theme.textPrimary(scheme))
                    .scrollContentBackground(.hidden)
                    .focused($focused)
                    .frame(minHeight: 200)
                    .padding(Space.x2)
            }
            .background(Theme.bgField(scheme), in: RoundedRectangle(cornerRadius: Radius.control))
            .overlay(
                RoundedRectangle(cornerRadius: Radius.control)
                    .stroke(focused ? Theme.borderStrong(scheme) : Theme.border(scheme), lineWidth: 1)
            )
        } else {
            TextField(placeholder, text: $draft, axis: .vertical)
                .font(.empBody)
                .foregroundStyle(Theme.textPrimary(scheme))
                .lineLimit(1...6)
                .focused($focused)
                .padding(Space.x3)
                .background(Theme.bgField(scheme), in: RoundedRectangle(cornerRadius: Radius.control))
                .overlay(
                    RoundedRectangle(cornerRadius: Radius.control)
                        .stroke(focused ? Theme.borderStrong(scheme) : Theme.border(scheme), lineWidth: 1)
                )
        }
    }
}


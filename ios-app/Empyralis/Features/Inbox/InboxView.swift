import SwiftUI
import UIKit

/// INBOX — "what needs you, across every agent, right now."
///
/// This screen used to list every open task in the workspace, which is a
/// DIFFERENT question from the one the web app answers under the same word.
/// The rule it now composes is `InboxNeedsYou.swift`, a port of the web's
/// own `inbox-needs-you.ts` — three ranked sources, never re-derived here.
/// See that file for why there are exactly three and why the task group
/// sorts oldest-first.
///
/// Reads from `WorkspaceStore`, never from the network directly — all three
/// sources are hydrated from disk at launch, so this is populated on the
/// first frame. Pull-to-refresh triggers a background sync; nothing blanks
/// while that runs.
///
/// Read-only about agents, deliberately: this app never composes a message
/// to one. Talking to an agent happens in its channel (Telegram/Slack).
///
/// NO ACCENT ANYWHERE ON THIS SCREEN, and that is a real difference from the
/// web rather than an omission. The web paints the notification icon with
/// `var(--accent)`; the iOS accent law is stricter — violet appears ONLY on
/// the single primary-action button in a view — and an Inbox row is not a
/// primary action. The three kinds separate by icon and by section instead,
/// with the warning tone reserved for the two that genuinely need attention.
struct InboxView: View {
    /// Owned by MainTabView so a deep link (notification tap, or a task URL
    /// from a channel message) can push a destination into this stack from
    /// outside the view.
    @Binding var path: NavigationPath

    @EnvironmentObject private var store: WorkspaceStore
    @EnvironmentObject private var session: SessionStore
    @Environment(\.colorScheme) private var scheme

    @State private var showSettings = false

    /// The caller's own account id — the same identifier space as a task's
    /// `assignee_user_id` and a notification's `recipient_user_id` (all three
    /// are `users.id`; auth's `_public_user_payload` emits it verbatim).
    /// Traced rather than assumed: a mismatch here is silent and would leave
    /// this screen permanently, plausibly empty.
    private var myUserId: String? { session.user?.id }

    private var groups: InboxNeedsYouGroups {
        planInboxNeedsYou(
            stuckTasks: store.tasks,
            notifications: store.notifications,
            blockedRuns: store.blockedRuns,
            userId: myUserId,
            // A row is tappable only when its real home exists locally.
            // Same meaning as the web's nil href: an unresolvable item
            // renders as plain text rather than as a link to nowhere.
            taskDestinationFor: { taskId in
                guard let taskId, store.task(taskId) != nil else { return nil }
                return .task(taskId: taskId)
            },
            agentDestinationFor: { installId in
                guard let installId, store.agent(installId) != nil else { return nil }
                return .agent(agentId: installId)
            }
        )
    }

    var body: some View {
        NavigationStack(path: $path) {
            ZStack {
                Theme.bgPage(scheme).ignoresSafeArea()
                content
            }
            .navigationTitle("Inbox")
            .toolbar {
                // Settings lives here rather than in the tab bar: the rail it
                // mirrors has four items, and a setting is a question people
                // have twice. The front door is the one place it belongs.
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        showSettings = true
                    } label: {
                        Image(systemName: "person.crop.circle")
                            .foregroundStyle(Theme.textSecondary(scheme))
                    }
                    .accessibilityLabel("Account and settings")
                }
            }
            .sheet(isPresented: $showSettings) { SettingsView() }
            .navigationDestination(for: TaskRoute.self) { route in
                TaskDetailView(taskId: route.taskId)
            }
            .navigationDestination(for: AgentRoute.self) { route in
                AgentDestinationView(agentId: route.agentId)
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        let groups = groups
        let total = inboxNeedsYouCount(groups)
        let sourceError = store.notificationsError ?? store.blockedRunsError

        if !store.hasLoadedOnce {
            if let error = store.loadError {
                // Nothing known AND the read failed. Never "all caught up".
                ScrollView {
                    EmptyStateView(
                        title: "Couldn't load your inbox",
                        message: error,
                        systemImage: "wifi.exclamationmark"
                    )
                    .padding(.top, Space.x10)
                }
                .refreshable { await store.refresh() }
            } else {
                // The ONLY state that may show a skeleton: nothing known yet.
                List {
                    ForEach(0..<6, id: \.self) { _ in
                        SkeletonRow().listRowBackground(Theme.bgPage(scheme))
                    }
                }
                .listStyle(.plain)
            }
        } else if total == 0, let sourceError {
            // We have a workspace, but at least one of the three sources
            // could not be read — so "nothing needs you" is a claim we are
            // not entitled to make.
            ScrollView {
                EmptyStateView(
                    title: "Couldn't load part of your inbox",
                    message: sourceError,
                    systemImage: "exclamationmark.triangle"
                )
                .padding(.top, Space.x10)
            }
            .refreshable { await store.refresh() }
        } else if total == 0 {
            ScrollView {
                EmptyStateView(
                    title: "You're all caught up",
                    message: "Mentions, task assignments, stuck work, and failed runs show up here.",
                    systemImage: "checkmark.circle"
                )
                .padding(.top, Space.x10)
            }
            .refreshable { await store.refresh() }
        } else {
            List {
                section(
                    title: "Needs your input",
                    items: groups.tasks,
                    error: nil,
                    symbol: "clock",
                    tone: Theme.warning(scheme)
                )
                section(
                    title: "Notifications",
                    items: groups.notifications,
                    error: store.notificationsError,
                    symbol: "at",
                    tone: Theme.textSecondary(scheme)
                )
                section(
                    title: "Failed runs",
                    items: groups.runs,
                    error: store.blockedRunsError,
                    symbol: "exclamationmark.triangle",
                    tone: Theme.warning(scheme)
                )
            }
            .listStyle(.plain)
            .refreshable { await store.refresh() }
        }
    }

    /// One titled group. Renders nothing when empty AND error-free — an empty
    /// section with nothing to say is just noise above sections that do have
    /// content, and the screen-level empty state already covers "nothing
    /// anywhere". An error on THIS source is shown inline, right where the
    /// missing rows would have been, rather than silently reading as "zero
    /// items".
    @ViewBuilder
    private func section(
        title: String,
        items: [InboxNeedsYouItem],
        error: String?,
        symbol: String,
        tone: Color
    ) -> some View {
        if !items.isEmpty || error != nil {
            Section {
                if let error {
                    Text("Couldn't load this section: \(error)")
                        .font(.empCaption)
                        .foregroundStyle(Theme.offline(scheme))
                        .listRowBackground(Theme.bgPage(scheme))
                }
                ForEach(items) { item in
                    row(item, symbol: symbol, tone: tone)
                        .listRowBackground(Theme.bgPage(scheme))
                }
            } header: {
                HStack(spacing: Space.x2) {
                    Text(title.uppercased())
                        .font(.empSectionHeader)
                        .foregroundStyle(Theme.textMuted(scheme))
                    if !items.isEmpty {
                        Text("\(items.count)")
                            .font(.empCaption)
                            .foregroundStyle(Theme.textMuted(scheme))
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func row(_ item: InboxNeedsYouItem, symbol: String, tone: Color) -> some View {
        switch item.destination {
        case .task(let taskId):
            NavigationLink(value: TaskRoute(taskId: taskId)) {
                InboxRow(item: item, symbol: symbol, tone: tone)
            }
            .simultaneousGesture(TapGesture().onEnded { markReadIfNotification(item) })
        case .agent(let agentId):
            NavigationLink(value: AgentRoute(agentId: agentId)) {
                InboxRow(item: item, symbol: symbol, tone: tone)
            }
        case nil:
            // No resolvable home — plain, untappable, still visible. Hiding
            // it would be worse: it is a real thing that needs the reader.
            InboxRow(item: item, symbol: symbol, tone: tone)
        }
    }

    /// Best-effort and fire-and-forget: navigation must never wait on this,
    /// and a failed mark-read must never block reaching the task — it just
    /// means the notification shows up again on the next refresh, which is
    /// the honest degradation rather than silently losing it.
    private func markReadIfNotification(_ item: InboxNeedsYouItem) {
        guard item.kind == .notification else { return }
        let id = String(item.id.dropFirst("notification:".count))
        Task { await store.markNotificationRead(id) }
    }
}

struct InboxRow: View {
    let item: InboxNeedsYouItem
    let symbol: String
    let tone: Color
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        HStack(alignment: .top, spacing: Space.x3) {
            Image(systemName: symbol)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(tone)
                .frame(width: 16)
                .padding(.top, 3)

            VStack(alignment: .leading, spacing: Space.x1) {
                Text(item.title)
                    .font(.empBody)
                    .foregroundStyle(Theme.textPrimary(scheme))
                    .lineLimit(2)

                if let detail = item.detail, !detail.isEmpty {
                    Text(detail)
                        .font(.empCaption)
                        .foregroundStyle(Theme.textMuted(scheme))
                        .lineLimit(2)
                }
            }

            Spacer(minLength: Space.x2)

            let stamp = inboxTimeAgo(item.timestamp)
            if !stamp.isEmpty {
                Text(stamp)
                    .font(.empCaption)
                    .foregroundStyle(Theme.textMuted(scheme))
            }
        }
        .padding(.vertical, Space.x1)
    }
}

/// Resolves an install id to the agent the detail view needs. Held here
/// rather than in the route so the route value stays a plain id — a pushed
/// destination that carries a whole model goes stale the moment the store
/// refreshes underneath it.
struct AgentDestinationView: View {
    let agentId: String
    @EnvironmentObject private var store: WorkspaceStore

    var body: some View {
        if let agent = store.agent(agentId) {
            AgentDetailView(agent: agent)
        } else {
            // Reachable only if the agent disappeared between the tap and
            // this frame. "Gone" and "still loading" are different facts and
            // the store has already loaded by the time a row was tappable.
            EmptyStateView(
                title: "Agent not found",
                message: "It may have been removed from this workspace.",
                systemImage: "questionmark.circle"
            )
        }
    }
}

/// The display rules are `TaskDates.timeAgo`'s — relative inside a week,
/// absolute beyond it — reused rather than re-invented. The wrapper exists
/// only because `TaskDates.parse` accepts zero or three fractional digits,
/// while `activity_ledger_service._iso_ts` emits Python's
/// `datetime.isoformat()`, i.e. SIX. Without the normalize fallback every
/// "Failed runs" timestamp renders blank.
func inboxTimeAgo(_ iso: String?) -> String {
    guard let raw = inboxNonEmpty(iso) else { return "" }
    let direct = TaskDates.timeAgo(raw)
    if !direct.isEmpty { return direct }
    return TaskDates.timeAgo(InboxDateParsing.normalize(raw))
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

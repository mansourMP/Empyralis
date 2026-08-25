import SwiftUI
import UIKit

struct MainTabView: View {
    @EnvironmentObject private var deepLinks: DeepLinkRouter
    @EnvironmentObject private var store: WorkspaceStore

    @State private var selectedTab: Tab = .inbox
    @State private var inboxPath = NavigationPath()

    enum Tab: Hashable { case inbox, search, projects, docs, agents, settings }

    var body: some View {
        TabView(selection: $selectedTab) {
            InboxView(path: $inboxPath)
                .tabItem { Label("Inbox", systemImage: "tray") }
                .tag(Tab.inbox)

            // Second, not last: search is the answer to "where is that
            // task", which is a question people have constantly and
            // Settings is a question they have twice.
            SearchView()
                .tabItem { Label("Search", systemImage: "magnifyingglass") }
                .tag(Tab.search)

            ProjectsView()
                .tabItem { Label("Projects", systemImage: "folder") }
                .tag(Tab.projects)

            DocumentsView()
                .tabItem { Label("Docs", systemImage: "doc.text") }
                .tag(Tab.docs)

            AgentsView()
                .tabItem { Label("Agents", systemImage: "cpu") }
                .tag(Tab.agents)

            SettingsView()
                .tabItem { Label("Settings", systemImage: "gearshape") }
                .tag(Tab.settings)
        }
        .onChange(of: deepLinks.pending) { _, link in
            guard let link else { return }
            route(link)
        }
        // A link that arrived before this view existed (cold launch from a
        // notification tap) is still pending — take it on appear rather
        // than waiting for a change that already happened.
        .onAppear {
            if let link = deepLinks.pending { route(link) }
        }
    }

    private func route(_ link: DeepLink) {
        switch link {
        case .task(_, let taskId):
            selectedTab = .inbox
            // Pushed by ID: TaskDetailView resolves it from the store, so
            // a task that isn't cached yet still opens and fills in on the
            // next sync rather than dead-ending.
            inboxPath.append(TaskRoute(taskId: taskId))
        case .document:
            selectedTab = .docs
        case .project:
            selectedTab = .projects
        case .agent:
            selectedTab = .agents
        }
        _ = deepLinks.consume()
    }
}

/// A typed route value — NavigationPath needs Hashable, and a bare String
/// would collide with any other string-typed destination added later.
struct TaskRoute: Hashable {
    let taskId: String
}

struct SettingsView: View {
    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var store: WorkspaceStore
    @EnvironmentObject private var push: PushManager
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.bgPage(scheme).ignoresSafeArea()

                List {
                    if let email = session.user?.email {
                        Section {
                            LabeledContent("Account", value: email).font(.empBody)
                        }
                        .listRowBackground(Theme.bgCard(scheme))
                    }

                    Section("Notifications") {
                        notificationRow
                    }
                    .listRowBackground(Theme.bgCard(scheme))

                    Section {
                        Button("Sign Out", role: .destructive) {
                            store.clear()
                            session.signOut()
                        }
                        .font(.empBody)
                    }
                    .listRowBackground(Theme.bgCard(scheme))
                }
                .scrollContentBackground(.hidden)
            }
            .navigationTitle("Settings")
            .task { await push.refreshAuthorizationState() }
        }
    }

    /// Five states, five different controls. A single toggle cannot express
    /// `denied` — it would flip on, deliver nothing, and never say why.
    @ViewBuilder
    private var notificationRow: some View {
        switch push.state {
        case .notDetermined:
            Button("Turn on notifications") {
                Task { await push.requestAuthorization() }
            }
            .font(.empBody)

        case .registering:
            HStack {
                Text("Turning on…").font(.empBody).foregroundStyle(Theme.textSecondary(scheme))
                Spacer()
                ProgressView()
            }

        case .authorized:
            statusLine("On", tone: Theme.online(scheme))

        case .provisional:
            statusLine("Quiet delivery", tone: Theme.textMuted(scheme))

        case .denied:
            // The OS will never prompt again, so an in-app button here
            // would be a control that cannot work. The only real action
            // left is the Settings app.
            VStack(alignment: .leading, spacing: Space.x2) {
                statusLine("Off", tone: Theme.textMuted(scheme))
                Text("Notifications are turned off for Empyralis in iOS Settings.")
                    .font(.empCaption)
                    .foregroundStyle(Theme.textMuted(scheme))
                Button("Open Settings") {
                    guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
                    UIApplication.shared.open(url)
                }
                .font(.empBody)
            }
            .padding(.vertical, Space.x1)

        case .failed(let message):
            // Distinct from `denied`: permission was granted and something
            // else broke, so retrying is a real action.
            VStack(alignment: .leading, spacing: Space.x2) {
                statusLine("Couldn't turn on", tone: Theme.offline(scheme))
                Text(message)
                    .font(.empCaption)
                    .foregroundStyle(Theme.textMuted(scheme))
                Button("Try again") {
                    Task { await push.requestAuthorization() }
                }
                .font(.empBody)
            }
            .padding(.vertical, Space.x1)
        }
    }

    private func statusLine(_ text: String, tone: Color) -> some View {
        HStack {
            Text("Push notifications").font(.empBody).foregroundStyle(Theme.textPrimary(scheme))
            Spacer()
            Text(text).font(.empSecondary).foregroundStyle(tone)
        }
    }
}

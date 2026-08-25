import SwiftUI
import UIKit

struct MainTabView: View {
    @EnvironmentObject private var deepLinks: DeepLinkRouter
    @EnvironmentObject private var store: WorkspaceStore

    @State private var selectedTab: Tab = .inbox
    @State private var inboxPath = NavigationPath()

    enum Tab: Hashable { case inbox, myWork, projects, agents, search }

    /// THE TAB BAR IS THE WEB RAIL PLUS SEARCH, and every difference from
    /// what shipped before is a web decision this app was contradicting:
    ///
    ///   Inbox · My work · Projects · Agents          the rail, in its order
    ///   Search                                        a phone affordance the
    ///                                                 web solves with ⌘K
    ///
    ///   My work   was MISSING entirely. It is the one question the phone
    ///             could not answer: what is assigned to me, across every
    ///             project.
    ///   Docs      was TOP-LEVEL, which contradicts the web's own ruling
    ///             that a cross-project document lens does not earn a rail
    ///             slot. Documents now live inside the project that owns
    ///             them (ProjectDetailView), mirroring PROJECT_TAB_VIEWS.
    ///   Settings  was a TAB. It is a question people have twice, so it is a
    ///             toolbar control on the front door instead — which is also
    ///             what keeps this at five tabs rather than six, where iOS
    ///             would collapse the tail into a "More" list nobody finds.
    var body: some View {
        TabView(selection: $selectedTab) {
            InboxView(path: $inboxPath)
                .tabItem { Label("Inbox", systemImage: "tray") }
                .tag(Tab.inbox)

            MyWorkView()
                .tabItem { Label("My work", systemImage: "checklist") }
                .tag(Tab.myWork)

            ProjectsView()
                .tabItem { Label("Projects", systemImage: "folder") }
                .tag(Tab.projects)

            AgentsView()
                .tabItem { Label("Agents", systemImage: "cpu") }
                .tag(Tab.agents)

            // Last: search is the answer to "where is that task", which is
            // a real and constant question, but it is a way of REACHING the
            // four surfaces above rather than a fifth one of its own.
            SearchView()
                .tabItem { Label("Search", systemImage: "magnifyingglass") }
                .tag(Tab.search)
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
            // Documents now live inside their project, and `DeepLink` does
            // not carry the project half of the URL it parsed — so the
            // honest landing spot is the project list, one tap from the
            // document, rather than a tab that no longer exists. Widening
            // the enum to carry `projectId` would let this push all the way
            // through; that is a change to the deep-link contract and its
            // tests, not a side effect of moving a tab.
            selectedTab = .projects
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

/// The Inbox's "Failed runs" rows open the agent that failed. Carries the
/// install id only — a route that carried the whole `Agent` would go stale
/// the moment the store refreshed underneath it.
struct AgentRoute: Hashable {
    let agentId: String
}

/// Presented as a SHEET from the Inbox's toolbar rather than as a tab — see
/// MainTabView's own note. It therefore needs its own way out, which a tab
/// never did.
struct SettingsView: View {
    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var store: WorkspaceStore
    @EnvironmentObject private var push: PushManager
    @Environment(\.colorScheme) private var scheme
    @Environment(\.dismiss) private var dismiss

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
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .font(.empBody)
                        .foregroundStyle(Theme.textSecondary(scheme))
                }
            }
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

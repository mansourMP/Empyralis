import SwiftUI

struct RootView: View {
    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var store: WorkspaceStore
    @Environment(\.colorScheme) private var scheme
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        Group {
            switch session.state {
            case .unknown:
                ZStack {
                    Theme.bgPage(scheme).ignoresSafeArea()
                    ProgressView()
                }
            case .signedOut:
                LoginView()
            case .signedIn:
                MainTabView()
            }
        }
        .task { await session.restoreSession() }
        // Binding is synchronous and hydrates from disk, so by the time the
        // tab view's first frame renders the lists already hold real content.
        .onChange(of: session.currentWorkspaceId) { _, workspaceId in
            guard let workspaceId else { return }
            store.bind(workspaceId: workspaceId)
            Task { await store.refresh() }
        }
        // Returning to the app refreshes in the background — the content
        // on screen never blanks while that happens.
        .onChange(of: scenePhase) { _, phase in
            guard phase == .active, session.state == .signedIn else { return }
            Task { await store.refresh() }
        }
    }
}

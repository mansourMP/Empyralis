import SwiftUI

struct RootView: View {
    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var store: WorkspaceStore
    @Environment(\.colorScheme) private var scheme
    @Environment(\.scenePhase) private var scenePhase

    /// Read once at construction rather than in the body — reading
    /// UserDefaults during render would flip the screen out from under
    /// someone the moment markSeen() lands.
    @State private var showWelcome = !WelcomeState.hasSeen

    var body: some View {
        Group {
            switch session.state {
            case .unknown:
                ZStack {
                    Theme.bgPage(scheme).ignoresSafeArea()
                    ProgressView()
                }
            case .signedOut:
                // The greeting comes before the ask, once per install. A
                // returning person who signed out gets the login screen
                // directly — greeting someone who just left is noise.
                if showWelcome {
                    WelcomeView {
                        WelcomeState.markSeen()
                        withAnimation(.easeOut(duration: 0.15)) { showWelcome = false }
                    }
                } else {
                    LoginView()
                }
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

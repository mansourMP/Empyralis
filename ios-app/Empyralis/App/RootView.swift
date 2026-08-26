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
                    // markSeen() happens inside WelcomeView itself (both its
                    // primary and secondary actions call it) — this closure
                    // only needs to reveal LoginView.
                    WelcomeView {
                        withAnimation(.easeOut(duration: 0.15)) { showWelcome = false }
                    }
                } else {
                    LoginView()
                }
            case .signedIn:
                MainTabView()
            }
        }
        // THE APP HAS NO SYSTEM BLUE IN IT. Left unset, SwiftUI tints every
        // control it owns — the selected tab, a plain Button in a List, the
        // pull-to-refresh spinner, the caret — with Apple's default blue,
        // which is a hue this product's design system never defines. It was
        // rendering on the tab bar of every screen.
        //
        // The replacement is NOT the accent: Theme's own header forbids the
        // violet on tab bars and selected rows by name. It is the primary
        // TEXT colour, which is the design system's own answer — selection
        // is weight and shape, never hue. Buttons that genuinely are a
        // view's primary action paint themselves (AuthPrimaryPillStyle, the
        // comment send button) and are unaffected by this; `role:
        // .destructive` still goes red, which is an iOS convention people
        // read as "careful", not as branding.
        .tint(Theme.textPrimary(scheme))
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

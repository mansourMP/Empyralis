import SwiftUI

@main
struct EmpyralisApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    @StateObject private var session: SessionStore
    @StateObject private var store: WorkspaceStore
    @StateObject private var push: PushManager
    @StateObject private var deepLinks: DeepLinkRouter

    init() {
        let session = SessionStore()
        let push = PushManager(session: session)
        let deepLinks = DeepLinkRouter()

        _session = StateObject(wrappedValue: session)
        _store = StateObject(wrappedValue: WorkspaceStore(session: session))
        _push = StateObject(wrappedValue: push)
        _deepLinks = StateObject(wrappedValue: deepLinks)

        // The delegate receives OS callbacks before any view exists, so it
        // needs these references at construction rather than via the
        // environment.
        AppDelegate.pushManager = push
        AppDelegate.deepLinkRouter = deepLinks
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(session)
                .environmentObject(store)
                .environmentObject(push)
                .environmentObject(deepLinks)
                // Universal links: a task URL an agent posted into Telegram
                // opens the app here instead of Safari.
                .onOpenURL { deepLinks.handle($0) }
        }
    }
}

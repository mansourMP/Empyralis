import XCTest

/// Shared logic for reaching the email/password login form from a fresh
/// launch — the ONE thing every UIDriver test file needs when the welcome/
/// login flow changes, instead of seven files each re-deriving it (which is
/// exactly how seven files broke the same way at once when "Get started"
/// stopped leading to the in-app form).
///
/// A launch lands in one of three states (`RootView.swift`'s own
/// `switch session.state`, plus its local `showWelcome` flag):
///
///   1. already signed in       — a Keychain session survived
///                                 `session.restoreSession()`; `MainTabView`
///                                 renders directly, no welcome, no login.
///   2. the welcome screen      — `WelcomeState.hasSeen` was false at
///                                 launch: logo + "Get started" + "Sign in
///                                 with email instead" (WelcomeView.swift).
///   3. the login form directly — welcome was already marked seen on an
///                                 earlier launch of this same install
///                                 (`WelcomeState.markSeen()` persists in
///                                 UserDefaults and deliberately survives
///                                 sign-out — see WelcomeView.swift's own
///                                 header), and no session is active.
///
/// "Get started" now opens `ASWebAuthenticationSession` — a SYSTEM Safari
/// sheet (out-of-process, and it raises a one-time consent alert on first
/// use) that XCUITest cannot reliably drive, and this harness must not try
/// to (see NativeWebLogin.swift). So this helper NEVER taps "Get started"
/// for navigation — the only door to an in-app, harness-drivable session is
/// the secondary **"Sign in with email instead"** link, exact label per
/// WelcomeView.swift.
///
/// A test whose specific purpose was "verify the 'Get started' button
/// starts a real, completed sign-in" can no longer be covered by this
/// harness — driving `ASWebAuthenticationSession` to a real authenticated
/// session isn't something XCUITest can do reliably from outside the
/// system sheet. (`GoogleButton.swift`'s `testConfiguredBuildStartsThe
/// GoogleFlow` shows the ceiling of what IS still verifiable for a flow
/// like this: that tapping the button reaches Apple's own consent alert,
/// proving the request was well-formed, without completing the sign-in.
/// Nothing in this file attempts that for "Get started" — none of the
/// seven files this repairs were testing the button itself, only using it
/// as a stepping stone to the email form, so no coverage is lost by
/// routing them around it entirely.)
enum SignInFlow {

    /// What a fresh launch resolved to, once the dust settles.
    enum LandingState {
        /// The tab bar is already on screen — nothing to reach.
        case alreadySignedIn
        /// The email/password form is on screen and ready to be filled.
        case emailFormReady
        /// Neither appeared within the allotted time — a genuine miss.
        case neither
    }

    /// Gets from wherever a fresh launch landed to the email/password form
    /// being on screen, tapping "Sign in with email instead" (never "Get
    /// started") if the welcome screen is showing. Safe to call regardless
    /// of which of the three states above is current — that is the whole
    /// point of centralizing this here rather than in each call site.
    static func reach(_ app: XCUIApplication, tabBarTimeout: TimeInterval = 3) -> LandingState {
        if app.tabBars.firstMatch.waitForExistence(timeout: tabBarTimeout) {
            return .alreadySignedIn
        }

        let getStarted = app.buttons["Get started"]
        if getStarted.waitForExistence(timeout: 8) {
            // The welcome screen is up. Tap the SECONDARY link, never the
            // primary button — "Get started" now opens the system Safari
            // sheet, which this harness cannot drive an authenticated
            // session through. Coordinate tap + retry, same as every other
            // button in this harness: a plain `.tap()` has repeatedly
            // resolved, reported hittable, and silently done nothing.
            let useEmailInstead = app.buttons["Sign in with email instead"]
            for _ in 0..<3 {
                useEmailInstead.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                if app.tabBars.firstMatch.exists { return .alreadySignedIn }
                if app.textFields.firstMatch.waitForExistence(timeout: 5) { return .emailFormReady }
            }
            return .neither
        }

        // No welcome screen rendered at all: either the login form is
        // already showing directly (welcome was marked seen on an earlier
        // launch of this install) or a session is already active.
        if app.tabBars.firstMatch.exists { return .alreadySignedIn }
        return app.textFields.firstMatch.waitForExistence(timeout: 6) ? .emailFormReady : .neither
    }

    /// Convenience for callers that only need to know "should I proceed" —
    /// `true` when either the email form is now on screen (fill it in) or a
    /// session is already active (nothing to fill); `false` only when
    /// neither happened, which the caller should treat as its own miss.
    @discardableResult
    static func reachEmailForm(_ app: XCUIApplication, tabBarTimeout: TimeInterval = 3) -> Bool {
        reach(app, tabBarTimeout: tabBarTimeout) != .neither
    }
}

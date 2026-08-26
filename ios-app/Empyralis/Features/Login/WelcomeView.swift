import SwiftUI

/// The first screen on a cold launch, before anything is asked.
///
/// Founder, looking at Linear's iOS app: pressing the button "just goes to
/// open... a page that seems like a Safari because it's default probably."
/// And on the screen itself: "There is a logo, there is not even a brand
/// name. There is just a brand logo and it says start or something like
/// this." So: the mark, ONE button, no wordmark, no kicker line — nothing
/// to read. Tapping it opens the real website in Safari
/// (`ASWebAuthenticationSession`, see `NativeWebLogin`) and every login
/// method the website offers works here for free, with no form of our own.
///
/// The email/password screen (`LoginView`) is still real and still needed —
/// the seeded test backend and anyone the website itself can't reach still
/// need a door — so it stays reachable behind a quiet secondary link rather
/// than being deleted. It is deliberately NOT the primary action: the
/// founder described the button going straight to Safari, not landing on a
/// form with two options.
///
/// Shown ONCE per install, not on every launch: after signing out, a
/// returning person wants a way in, not a greeting again. `hasSeen` lives in
/// UserDefaults rather than the Keychain deliberately — it is a UI
/// preference, not a credential, and it SHOULD disappear when the app is
/// deleted so a reinstall gets the full first-run experience again.
struct WelcomeView: View {
    /// Reveals the existing email/password screen. The fallback door, kept
    /// reachable but never primary — see this file's own header.
    let onUseEmailInstead: () -> Void

    @EnvironmentObject private var session: SessionStore
    @Environment(\.colorScheme) private var scheme
    @State private var isSigningIn = false

    var body: some View {
        ZStack {
            Theme.bgPage(scheme).ignoresSafeArea()

            VStack(spacing: 0) {
                Spacer()

                BrandMarkTile(size: 160)

                Spacer()
                Spacer()

                // FOUR HONEST STATES, never collapsed into one message:
                //   1. cancelled the sheet            → nothing rendered here
                //   2. login failed ON THE WEBSITE     → the website's OWN
                //      error, shown inline inside the sheet itself; this
                //      screen never duplicates it (see NativeWebLoginError's
                //      own note on why that case has no branch here)
                //   3. the code exchange failed        → this message
                //   4. the callback's state didn't match → this message,
                //      worded distinctly (SessionStore.loginWithNativeWebFlow
                //      is where the two are told apart)
                if let error = session.lastErrorMessage {
                    Text(error)
                        .font(.empSecondary)
                        .foregroundStyle(Theme.offline(scheme))
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, Space.x6)
                        .padding(.bottom, Space.x4)
                }

                Button(action: getStarted) {
                    if isSigningIn {
                        ProgressView().tint(Theme.textPrimary(scheme))
                    } else {
                        Text("Get started")
                    }
                }
                .buttonStyle(AuthSecondaryPillStyle())
                .disabled(isSigningIn)
                .padding(.horizontal, Space.x5)

                Button("Sign in with email instead", action: useEmailInstead)
                    .font(.empSecondary)
                    .foregroundStyle(Theme.textMuted(scheme))
                    .disabled(isSigningIn)
                    .padding(.top, Space.x5)
                    .padding(.bottom, Space.x6)
            }
        }
    }

    private func getStarted() {
        guard !isSigningIn else { return }
        WelcomeState.markSeen()
        isSigningIn = true
        Task {
            await session.loginWithNativeWebFlow()
            isSigningIn = false
        }
    }

    private func useEmailInstead() {
        WelcomeState.markSeen()
        // Clears any error left over from a failed native-web attempt — that
        // message ("sign-in code invalid", a state mismatch, ...) describes
        // the Safari door and would be a non-sequitur sitting over the
        // email/password fields on the next screen.
        session.lastErrorMessage = nil
        onUseEmailInstead()
    }
}

/// One-line store for whether the welcome screen has been shown. Kept beside
/// the view it serves rather than in SessionStore — it is not session state
/// and must survive a sign-out.
enum WelcomeState {
    private static let key = "empyralis.hasSeenWelcome.v1"

    static var hasSeen: Bool {
        UserDefaults.standard.bool(forKey: key)
    }

    static func markSeen() {
        UserDefaults.standard.set(true, forKey: key)
    }
}

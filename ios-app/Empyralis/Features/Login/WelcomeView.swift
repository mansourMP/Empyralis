import SwiftUI

/// The first screen on a cold launch, before anything is asked.
///
/// Its whole job is to say what this is and offer ONE action. No form, no
/// fields, nothing to read. The mark is the hero because it is the same
/// thing the person just tapped on their home screen — the app should look
/// continuous with its own icon rather than dropping straight into a login
/// form, which is the version that reads as a website in a shell.
///
/// Shown ONCE per install, not on every launch: after signing out, a
/// returning person wants the sign-in screen, not a greeting. `hasSeen`
/// lives in UserDefaults rather than the Keychain deliberately — it is a
/// UI preference, not a credential, and it SHOULD disappear when the app
/// is deleted so a reinstall gets the full first-run experience again.
struct WelcomeView: View {
    let onContinue: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        ZStack {
            Theme.bgPage(scheme).ignoresSafeArea()

            VStack(spacing: 0) {
                Spacer()

                BrandMarkTile(size: 160)

                VStack(spacing: Space.x1) {
                    Text("Welcome to")
                        .font(.system(size: 17, weight: .medium))
                        .foregroundStyle(Theme.textMuted(scheme))
                    Text("Empyralis")
                        .font(.system(size: 34, weight: .bold))
                        .foregroundStyle(Theme.textPrimary(scheme))
                }
                .padding(.top, Space.x6)

                Spacer()
                Spacer()

                Button("Get started", action: onContinue)
                    .buttonStyle(AuthSecondaryPillStyle())
                    .padding(.horizontal, Space.x5)
                    .padding(.bottom, Space.x6)
            }
        }
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

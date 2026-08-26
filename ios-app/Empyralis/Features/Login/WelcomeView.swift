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
                        // UIFontMetrics via Theme's empScaled, relativeTo:
                        // .subheadline — this is a small kicker line above
                        // the hero wordmark, and subheadline is the style
                        // Apple defines for exactly that role. Its own
                        // default (15) sits close to this base (17), so the
                        // curve is well matched to the size actually used.
                        .font(Font.empScaled(17, weight: .medium, relativeTo: .subheadline))
                        .foregroundStyle(Theme.textMuted(scheme))
                    Text("Empyralis")
                        // relativeTo: .largeTitle — 34pt is Apple's own
                        // largeTitle default size exactly, and this line
                        // has a huge (100%) base-size cushion over "Welcome
                        // to" above it, so even largeTitle's flat curve
                        // (the flattest of all the title-class curves)
                        // cannot plausibly invert that ordering.
                        .font(Font.empScaled(34, weight: .bold, relativeTo: .largeTitle))
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

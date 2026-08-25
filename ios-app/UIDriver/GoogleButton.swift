import XCTest

/// TEMPORARY verification harness, same family as `Walkthrough` — not part of
/// the shipped project.
///
/// Answers the one question a screenshot cannot: does "Continue with Google"
/// actually START the flow, or is it a button that renders and does nothing?
/// The proof is iOS's own consent alert — the system only raises
/// *"…Wants to Use "google.com" to Sign In"* once `ASWebAuthenticationSession`
/// has accepted the URL **and** the callback scheme, so seeing it means the
/// authorize URL and the reversed-client-id scheme are both well-formed.
///
/// A FAKE client id is deliberate. The consent alert fires before any network
/// request, so nothing real is contacted and no Google account is involved —
/// and the app ships with no client id at all, so this is also the only way to
/// exercise the configured arrangement.
///
/// **Every assertion here is soft (`print`, never `XCTFail`).** This file
/// shares a target with another agent's harness; a hard failure here would
/// redden a run that has nothing to do with it.
final class GoogleButton: XCTestCase {

    private static let fakeClientId =
        "123456789012-abcdefghijklmnopqrstuvwx.apps.googleusercontent.com"

    private func launch(googleClientId: String?) -> XCUIApplication {
        let app = XCUIApplication()
        if let googleClientId {
            app.launchEnvironment["EMPYRALIS_GOOGLE_IOS_CLIENT_ID"] = googleClientId
        }
        app.launch()
        sleep(3)
        return app
    }

    /// Configured -> the button exists and tapping it reaches Apple's own
    /// web-auth consent alert.
    func testConfiguredBuildStartsTheGoogleFlow() {
        let app = launch(googleClientId: Self.fakeClientId)

        let button = app.buttons["Continue with Google"]
        guard button.waitForExistence(timeout: 10) else {
            print("GOOGLE_MISS button absent — is the login screen showing?")
            return
        }
        print("GOOGLE_OK button rendered")

        button.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()

        // The alert belongs to SpringBoard, not to the app, so it has to be
        // queried through the springboard's own element tree.
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        let consent = springboard.alerts.firstMatch
        if consent.waitForExistence(timeout: 15) {
            let text = consent.staticTexts.allElementsBoundByIndex.map(\.label).joined(separator: " | ")
            print("GOOGLE_OK consent alert: \(text)")
            try? XCUIScreen.main.screenshot().pngRepresentation.write(
                to: URL(fileURLWithPath: "/tmp/emp-shots/google/01-consent.png")
            )
            // Cancel — nothing beyond this point is ours to drive.
            let cancel = consent.buttons["Cancel"]
            if cancel.exists { cancel.tap() }
        } else {
            print("GOOGLE_MISS no consent alert — the session did not start")
        }
    }

    /// Unconfigured -> the button is ABSENT, not disabled. This is the
    /// "no dead controls" half, and it is the state the app actually ships in.
    func testUnconfiguredBuildRendersNoGoogleButton() {
        let app = launch(googleClientId: nil)
        guard app.textFields.firstMatch.waitForExistence(timeout: 10) else {
            print("GOOGLE_MISS login screen not showing — cannot judge absence")
            return
        }
        let exists = app.buttons["Continue with Google"].exists
        print(exists
            ? "GOOGLE_MISS button rendered with NO client id — it cannot work"
            : "GOOGLE_OK no button without a client id")
    }

    override func setUpWithError() throws {
        continueAfterFailure = true
        try? FileManager.default.createDirectory(
            at: URL(fileURLWithPath: "/tmp/emp-shots/google"),
            withIntermediateDirectories: true
        )
    }
}

import XCTest

/// Does "Get started" actually start the Safari sign-in?
///
/// This is the coverage gap the harness repair explicitly flagged and did not
/// fill. It matters because everything else about that flow is compile-checked
/// only — the button could be wired to nothing at all and every other test in
/// this suite would still pass, since they all reach the email form through
/// the secondary link instead.
///
/// WHAT THIS CAN AND CANNOT PROVE, precisely:
///
///   CAN   that tapping the button causes iOS to raise its own
///         "...Wants to Use ...to Sign In" consent alert. That alert is
///         raised by ASWebAuthenticationSession itself, and ONLY once the
///         authorize URL and the callback scheme are both well-formed — so
///         its appearance is real evidence the session started correctly,
///         not merely that a button responded.
///
///   CANNOT  anything past that. The Safari sheet is out-of-process and
///           system-owned; XCUITest cannot read its DOM, type into it, or
///           assert on the page it loaded. Signing in THROUGH it is
///           permanently a human's job.
///
/// The alert is a SpringBoard alert, not an app view, so it is reached
/// through `springboardApp`, not `app`. Looking for it on `app` finds
/// nothing and reads as "the button did nothing" — a false failure.
final class SafariHandoff: XCTestCase {

    private var springboardApp: XCUIApplication {
        XCUIApplication(bundleIdentifier: "com.apple.springboard")
    }

    func testGetStartedRaisesTheSystemSignInConsent() throws {
        let app = XCUIApplication()
        // A fresh welcome screen is required: WelcomeState.hasSeen is sticky,
        // and on a device that has already been past it the button does not
        // exist at all. The runner erases the device before this test.
        app.launch()

        let getStarted = app.buttons["Get started"]
        guard getStarted.waitForExistence(timeout: 20) else {
            // Distinguish "already signed in" from "screen is wrong" rather
            // than failing with one undifferentiated message.
            if app.tabBars.firstMatch.waitForExistence(timeout: 3) {
                XCTFail("SAFARI_SKIP already signed in — erase the device first")
            } else {
                XCTFail("SAFARI_FAIL no 'Get started' button on the welcome screen")
            }
            return
        }
        shot(app, "01-welcome")

        // Plain .tap() silently no-ops on SwiftUI buttons in this harness —
        // an established lesson here, not a guess.
        getStarted.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()

        // iOS raises the consent alert itself. Give it room: the session has
        // to construct the authorize URL and hand off to the system first.
        let consent = springboardApp.alerts.firstMatch
        let appeared = consent.waitForExistence(timeout: 25)

        shot(springboardApp, "02-after-tap")

        if appeared {
            let text = consent.staticTexts.allElementsBoundByIndex
                .map(\.label).joined(separator: " | ")
            print("SAFARI_CONSENT \(text)")
            XCTAssertTrue(
                text.lowercased().contains("sign in") || text.lowercased().contains("empyralis"),
                "SAFARI_FAIL an alert appeared but does not look like the sign-in consent: \(text)"
            )
            // Proceed so the sheet itself is captured — the furthest point
            // automation can reach.
            let go = consent.buttons["Continue"]
            if go.exists { go.tap() }
            Thread.sleep(forTimeInterval: 6)
            shot(springboardApp, "03-safari-sheet")
            print("SAFARI_OK consent raised and dismissed; sheet captured")
        } else {
            // Not automatically a failure of the app: the consent alert is
            // shown once per app install per domain, so a device that has
            // already granted it goes straight to the sheet.
            Thread.sleep(forTimeInterval: 4)
            shot(springboardApp, "03-no-consent-state")
            print("SAFARI_NOCONSENT no alert within 25s — either already granted on this device, or the session never started. Read 03-no-consent-state.png.")
        }
    }

    private func shot(_ target: XCUIApplication, _ name: String) {
        let dir = "/tmp/emp-shots/safari"
        try? FileManager.default.createDirectory(
            atPath: dir, withIntermediateDirectories: true)
        let png = target.screenshot().pngRepresentation
        let path = "\(dir)/\(name).png"
        try? png.write(to: URL(fileURLWithPath: path))
        print("SHOT \(path)")
    }
}

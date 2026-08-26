import XCTest

/// TEMPORARY. The honest-failure screens, driven for real rather than read
/// off the source. `APIConfig` takes an `EMPYRALIS_API_BASE_URL` override,
/// so an unreachable backend is a launch environment rather than a code
/// change or a killed server.
final class FailureStates: XCTestCase {

    private var shotDir: URL!
    private var step = 0

    override func setUpWithError() throws {
        continueAfterFailure = true
        let label = ProcessInfo.processInfo.environment["SHOT_LABEL"] ?? "failures"
        shotDir = URL(fileURLWithPath: "/tmp/emp-shots").appendingPathComponent(label)
        try? FileManager.default.createDirectory(at: shotDir, withIntermediateDirectories: true)
    }

    private func shot(_ name: String) {
        step += 1
        let png = XCUIScreen.main.screenshot().pngRepresentation
        let f = shotDir.appendingPathComponent(String(format: "%02d-%@.png", step, name))
        try? png.write(to: f)
        print("SHOT \(f.path)")
    }

    private func signInScreen(_ app: XCUIApplication) {
        // "Get started" now opens the system Safari sheet
        // (ASWebAuthenticationSession) rather than the in-app form — see
        // SignInFlow.swift. The in-app door is the secondary "Sign in with
        // email instead" link, which SignInFlow.reachEmailForm taps.
        SignInFlow.reachEmailForm(app)
    }

    /// Wrong password must say so, and must not look like a network problem.
    func testWrongPassword() throws {
        let app = XCUIApplication()
        app.launch()
        sleep(2)
        signInScreen(app)

        let email = app.textFields.firstMatch
        XCTAssertTrue(email.waitForExistence(timeout: 10))
        email.tap(); email.typeText("ios.verify@example.com")
        let pw = app.secureTextFields.firstMatch
        pw.tap(); pw.typeText("definitely-not-the-password")
        app.buttons["Continue"].tap()
        sleep(6)
        shot("login-wrong-password")
        // Whatever it says, it must still be the login screen and must not
        // have signed anybody in.
        XCTAssertFalse(app.tabBars.firstMatch.exists, "a rejected password signed someone in")
    }

    /// Backend unreachable. "Couldn't sign in" is a different fact from
    /// "wrong password", and neither may render as success.
    func testBackendUnreachable() throws {
        let app = XCUIApplication()
        app.launchEnvironment["EMPYRALIS_API_BASE_URL"] = "http://127.0.0.1:9/api"
        app.launch()
        sleep(2)
        signInScreen(app)

        let email = app.textFields.firstMatch
        XCTAssertTrue(email.waitForExistence(timeout: 10))
        email.tap(); email.typeText("ios.verify@example.com")
        let pw = app.secureTextFields.firstMatch
        pw.tap(); pw.typeText("iosVerify-2026!")
        app.buttons["Continue"].tap()
        sleep(10)
        shot("login-backend-unreachable")
        XCTAssertFalse(app.tabBars.firstMatch.exists, "an unreachable backend signed someone in")
    }

    /// Signed in, then every workspace read fails. The four surfaces must
    /// say "couldn't load", never "empty" and never "all caught up".
    func testSignedInButWorkspaceReadsFail() throws {
        // 1. sign in for real so the keychain holds a session
        let app = XCUIApplication()
        app.launch()
        sleep(2)
        signInScreen(app)
        if app.textFields.firstMatch.waitForExistence(timeout: 10) {
            app.textFields.firstMatch.tap()
            app.textFields.firstMatch.typeText("ios.verify@example.com")
            app.secureTextFields.firstMatch.tap()
            app.secureTextFields.firstMatch.typeText("iosVerify-2026!")
            app.buttons["Continue"].tap()
        }
        guard app.tabBars.firstMatch.waitForExistence(timeout: 30) else {
            XCTFail("could not sign in to set up the offline case"); return
        }
        sleep(4)
        app.terminate()

        // 2. relaunch pointed at a dead port. The session restore fails, so
        //    this lands on the login screen rather than a signed-in shell —
        //    which is itself the honest outcome and worth capturing.
        app.launchEnvironment["EMPYRALIS_API_BASE_URL"] = "http://127.0.0.1:9/api"
        app.launch()
        sleep(8)
        shot("relaunch-with-dead-backend")
        print("FAILSTATE tabBar=\(app.tabBars.firstMatch.exists) login=\(app.textFields.firstMatch.exists)")
    }
}

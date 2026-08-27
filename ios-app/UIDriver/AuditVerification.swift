import XCTest

/// TEMPORARY, one-off verification for the three ios/audit-fixes findings
/// (see the branch's own commits). Not part of the house Walkthrough/
/// Supplementary/FailureStates rotation — this file exists to be run once
/// and read, not to stay in CI.
///
/// Uses the SAME `/tmp/emp-ctl` REQUEST/ACK handshake VerificationGaps.swift
/// established (a standalone host watcher for THIS run, not a shared
/// process — see audit_hostctl.sh in the scratchpad), because opening a
/// deep-link URL needs `xcrun simctl openurl`, which XCUITest cannot invoke
/// itself.
final class AuditVerification: XCTestCase {

    private var app: XCUIApplication!
    private var shotDir: URL!
    private var step = 0
    private var misses: [String] = []
    private static let ctlDir = URL(fileURLWithPath: "/tmp/emp-ctl")

    override func setUpWithError() throws {
        continueAfterFailure = true
        let label = ProcessInfo.processInfo.environment["SHOT_LABEL"] ?? "audit"
        shotDir = URL(fileURLWithPath: "/tmp/emp-shots").appendingPathComponent(label)
        try? FileManager.default.createDirectory(at: shotDir, withIntermediateDirectories: true)
        app = XCUIApplication()
    }

    // MARK: - plumbing (same idioms as Supplementary.swift / VerificationGaps.swift)

    private func shot(_ name: String) {
        step += 1
        let png = XCUIScreen.main.screenshot().pngRepresentation
        let f = shotDir.appendingPathComponent(String(format: "%02d-%@.png", step, name))
        try? png.write(to: f)
        print("SHOT \(f.path)")
    }

    private func miss(_ s: String) { misses.append(s); print("AUDIT_MISS \(s)") }

    private func hasFocus(_ e: XCUIElement) -> Bool {
        (e.value(forKey: "hasKeyboardFocus") as? Bool) ?? false
    }

    private func type(into field: XCUIElement, _ text: String) {
        for _ in 0..<4 {
            if !hasFocus(field) {
                field.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                usleep(700_000)
            }
            if hasFocus(field) { field.typeText(text); return }
            sleep(1)
        }
        miss("could not focus a field")
    }

    @discardableResult
    private func tab(_ index: Int, expect navBar: String) -> Bool {
        let bar = app.tabBars.firstMatch
        guard bar.waitForExistence(timeout: 10), bar.buttons.count > index else { return false }
        for _ in 0..<3 {
            bar.buttons.element(boundBy: index).coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            if app.navigationBars[navBar].waitForExistence(timeout: 8) { sleep(2); return true }
            sleep(2)
        }
        miss("tab \(index) never reached '\(navBar)'")
        return false
    }

    @discardableResult
    private func tapUntil(_ e: XCUIElement, name: String, timeout: TimeInterval = 8,
                          _ condition: @escaping () -> Bool) -> Bool {
        for _ in 0..<4 {
            if condition() { return true }
            e.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            let deadline = Date().addingTimeInterval(timeout)
            while Date() < deadline {
                if condition() { sleep(1); return true }
                usleep(300_000)
            }
        }
        miss("tapping '\(name)' never produced its result")
        return false
    }

    @discardableResult
    private func openURL(_ url: String, timeout: TimeInterval = 20) -> Bool {
        let req = Self.ctlDir.appendingPathComponent("REQUEST-open-url")
        let ack = Self.ctlDir.appendingPathComponent("ACK-open-url")
        try? FileManager.default.removeItem(at: ack)
        try? url.write(to: req, atomically: true, encoding: .utf8)
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if FileManager.default.fileExists(atPath: ack.path) { return true }
            usleep(200_000)
        }
        return false
    }

    /// GUARANTEES a signed-out app, regardless of whatever session this
    /// simulator's Keychain carried in from earlier UIDriver runs. Without
    /// this, `SignInFlow.reachEmailForm` reports `.alreadySignedIn` and a
    /// test silently proceeds against a LEFTOVER account instead of the one
    /// it asked to sign in as — confirmed live: the first pass of this file
    /// did exactly that, and every screenshot showed someone else's cached
    /// workspace. `xcrun simctl erase` does NOT clear the Keychain, so
    /// driving the app's own Sign Out is the only reliable door.
    private func ensureSignedOut() {
        guard app.tabBars.firstMatch.waitForExistence(timeout: 5) else { return } // already signed out
        _ = tab(0, expect: "Inbox")
        let gear = app.buttons["Account and settings"]
        guard gear.waitForExistence(timeout: 8) else { miss("no Account and settings button"); return }
        gear.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        guard app.navigationBars["Settings"].waitForExistence(timeout: 8) else {
            miss("Settings did not open for sign-out"); return
        }
        let signOut = app.buttons["Sign Out"]
        guard signOut.waitForExistence(timeout: 5) else { miss("no Sign Out button"); return }
        tapUntil(signOut, name: "Sign Out") { !self.app.tabBars.firstMatch.exists }
        sleep(1)
    }

    /// Reaches the email form (never already-signed-in, thanks to
    /// ensureSignedOut above) and signs in as a specific account.
    private func signIn(email: String, password: String) {
        ensureSignedOut()
        XCTAssertTrue(SignInFlow.reachEmailForm(app), "never reached the email/password form")
        XCTAssertFalse(app.tabBars.firstMatch.exists, "ensureSignedOut did not actually sign anyone out")
        let emailField = app.textFields.firstMatch
        XCTAssertTrue(emailField.waitForExistence(timeout: 10))
        type(into: emailField, email)
        let pw = app.secureTextFields.firstMatch
        XCTAssertTrue(pw.waitForExistence(timeout: 5))
        type(into: pw, password)
        tapUntil(app.buttons["Continue"], name: "Continue") { self.app.tabBars.firstMatch.exists }
    }

    // MARK: - Finding 1 (permanent skeleton) + Finding 2 (viewer write controls)

    /// Chained deliberately: Finding 1's own "recovery" step (relaunch
    /// against the real backend and confirm normal load) is exactly the
    /// state Finding 2 needs to drill into a task as the same viewer.
    func testFinding1LoadFailureThenFinding2ViewerControls() throws {
        // ---- Finding 1a: first-ever load, workspace reads unreachable ----
        // The discriminating proxy (127.0.0.1:18081) passes /api/auth/*
        // through to the real seeded backend so LOGIN succeeds, and drops
        // every other request outright -- the one combination a flat dead
        // port cannot reach, because session-restore itself needs the
        // network and would bounce to the login screen first.
        app.launchEnvironment["EMPYRALIS_API_BASE_URL"] = "http://127.0.0.1:18081/api"
        app.launch(); sleep(2)
        signIn(email: "ios.audit.viewer@example.com", password: "AuditViewer-2026!")
        XCTAssertTrue(app.tabBars.firstMatch.waitForExistence(timeout: 25), "login through the proxy did not reach the tab bar")
        sleep(3) // let the doomed refresh() finish failing

        XCTAssertTrue(tab(2, expect: "Projects"))
        sleep(1)
        shot("finding1-projects-error")
        XCTAssertTrue(app.staticTexts["Couldn't load projects"].waitForExistence(timeout: 6),
                      "Projects did not show the honest error title")

        XCTAssertTrue(tab(3, expect: "Agents"))
        sleep(1)
        shot("finding1-agents-error")
        XCTAssertTrue(app.staticTexts["Couldn't load agents"].waitForExistence(timeout: 6),
                      "Agents did not show the honest error title")

        // Pull-to-refresh must exist and be usable on the error screen.
        let scrollArea = app.scrollViews.firstMatch
        if scrollArea.waitForExistence(timeout: 3) {
            let start = scrollArea.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.3))
            let end = scrollArea.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.9))
            start.press(forDuration: 0.05, thenDragTo: end)
        } else {
            miss("no scroll view on the Agents error screen to pull-to-refresh from")
        }
        sleep(2)
        shot("finding1-agents-after-pull-refresh")

        // ---- Finding 1b: recovery -- relaunch against the REAL backend ----
        app.terminate()
        app.launchEnvironment["EMPYRALIS_API_BASE_URL"] = "http://127.0.0.1:8001/api"
        app.launch(); sleep(3)
        XCTAssertTrue(app.tabBars.firstMatch.waitForExistence(timeout: 25), "recovery relaunch did not reach the tab bar (session should have survived)")
        sleep(3)
        XCTAssertTrue(tab(2, expect: "Projects"))
        sleep(2)
        shot("finding1-projects-recovered")
        XCTAssertFalse(app.staticTexts["Couldn't load projects"].exists, "still showing the error after recovery")

        // ---- Finding 2: drill into a real task as this VIEWER ----
        let projectRow = app.staticTexts["General"]
        XCTAssertTrue(projectRow.waitForExistence(timeout: 10), "the viewer's own project row did not appear")
        projectRow.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        sleep(2)
        let taskRow = app.staticTexts["Audit viewer-role verification task"]
        XCTAssertTrue(taskRow.waitForExistence(timeout: 10), "the seeded task did not appear for the viewer")
        taskRow.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        sleep(2)
        shot("finding2-task-detail-as-viewer")

        // The write affordances must be ABSENT (not just disabled).
        XCTAssertFalse(app.buttons["New sub-task"].exists || app.staticTexts["Add sub-task"].exists,
                       "viewer sees Add sub-task")
        XCTAssertFalse(app.images["pencil"].exists, "viewer sees an edit pencil (title/description edit)")
        // Property rows still render (read-only) -- the LABEL and the current
        // VALUE stay visible; only the picker affordance is gone.
        XCTAssertTrue(app.staticTexts["Status"].exists, "the Status label should still render read-only")
        XCTAssertTrue(app.staticTexts["Priority"].exists, "the Priority label should still render read-only")
        // No comment composer -- fleet_comment_task also requires member.
        XCTAssertFalse(app.textFields["Comment"].exists, "viewer sees the comment composer")

        print("AUDIT12_DONE misses=\(misses.count) \(misses)")
    }

    // MARK: - Finding 3 (deep links land on the specific thing)

    func testFinding3DeepLinksResolveRealDestinations() throws {
        let ws = "ws_6d2846c045de"
        app.launchEnvironment["EMPYRALIS_API_BASE_URL"] = "http://127.0.0.1:8001/api"
        app.launch(); sleep(2)
        signIn(email: "ios.verify@example.com", password: "iosVerify-2026!")
        XCTAssertTrue(app.tabBars.firstMatch.waitForExistence(timeout: 25), "owner sign-in did not reach the tab bar")
        sleep(3)

        // .task -> the exact task, not the Inbox list
        XCTAssertTrue(openURL("empyralis://empyralis.ai/w/\(ws)/projects/project_6d02c813834b43aa/tasks/task_4a8f29dd3d8e4a75"),
                      "host never acked the task openurl request")
        sleep(3)
        shot("finding3-task-link")
        XCTAssertTrue(app.staticTexts["Audit viewer-role verification task"].waitForExistence(timeout: 10),
                      ".task deep link did not land on the specific task")

        // Back to Inbox before the next link, so each destination is a fresh push.
        _ = tab(0, expect: "Inbox")

        // .project -> the exact project, not the Projects list
        XCTAssertTrue(openURL("empyralis://empyralis.ai/w/\(ws)/projects/project_6d02c813834b43aa"),
                      "host never acked the project openurl request")
        sleep(3)
        shot("finding3-project-link")
        XCTAssertTrue(app.navigationBars["General"].waitForExistence(timeout: 10),
                      ".project deep link did not land on the specific project (expected nav title 'General')")

        // .agent -> the exact agent, not the Agents list
        XCTAssertTrue(openURL("empyralis://empyralis.ai/w/\(ws)/agents/ainstall_33c422f2041340e6"),
                      "host never acked the agent openurl request")
        sleep(3)
        shot("finding3-agent-link")
        XCTAssertTrue(app.staticTexts["MAN-Audit Deep Link Agent"].waitForExistence(timeout: 10),
                      ".agent deep link did not land on the specific agent")

        print("AUDIT3_DONE misses=\(misses.count) \(misses)")
    }
}

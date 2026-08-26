import XCTest

/// TEMPORARY verification harness for Settings ▸ Workspace / Connections —
/// the MCP Simulator panel this task would normally use for live-device
/// screenshots was unavailable this session (crashed and gave up retrying),
/// so this reuses the same `xcodebuild test`-driven pattern the rest of
/// `UIDriver/` already established for exactly this purpose (see
/// `Walkthrough.swift`/`Supplementary.swift`).
final class SettingsCheck: XCTestCase {

    private var app: XCUIApplication!
    private var shotDir: URL!
    private var step = 0
    private var misses: [String] = []

    override func setUpWithError() throws {
        continueAfterFailure = true
        let label = ProcessInfo.processInfo.environment["SHOT_LABEL"] ?? "settings"
        let base = URL(fileURLWithPath: "/tmp/emp-shots").appendingPathComponent(label)
        try? FileManager.default.removeItem(at: base)
        try? FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        shotDir = base
        app = XCUIApplication()
    }

    private func shot(_ name: String) {
        step += 1
        let png = XCUIScreen.main.screenshot().pngRepresentation
        try? png.write(to: shotDir.appendingPathComponent(String(format: "%02d-%@.png", step, name)))
        print("SHOT \(shotDir.path)/\(String(format: "%02d-%@.png", step, name))")
    }

    private func miss(_ s: String) { misses.append(s); print("SETTINGS_MISS \(s)") }
    private func rows() -> XCUIElementQuery { app.collectionViews.buttons }

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
    private func tapRowByLabel(_ text: String, waitFor: @escaping () -> Bool) -> Bool {
        let e = rows().matching(NSPredicate(format: "label CONTAINS[c] %@", text)).firstMatch
        if !e.waitForExistence(timeout: 8) || !e.isHittable {
            for _ in 0..<4 { app.swipeDown() }
            usleep(500_000)
            var ok = false
            for _ in 0..<8 {
                if e.exists && e.isHittable { ok = true; break }
                app.swipeUp(); usleep(400_000)
            }
            if !ok && !e.exists { miss("no row '\(text)'"); return false }
        }
        for _ in 0..<3 {
            e.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            let deadline = Date().addingTimeInterval(8)
            while Date() < deadline { if waitFor() { sleep(1); return true }; usleep(400_000) }
            sleep(1)
        }
        miss("row '\(text)' never opened")
        return false
    }

    private func back() {
        let b = app.navigationBars.buttons.element(boundBy: 0)
        if b.exists { b.tap(); sleep(2) }
    }

    private func signIn() {
        app.launch(); sleep(2)
        // "Get started" now opens the system Safari sheet
        // (ASWebAuthenticationSession) rather than the in-app form — see
        // SignInFlow.swift. The in-app door is the secondary "Sign in with
        // email instead" link, which SignInFlow.reachEmailForm taps.
        if !SignInFlow.reachEmailForm(app) {
            miss("could not reach the login form from the welcome screen")
        }
        let email = app.textFields.firstMatch
        if email.waitForExistence(timeout: 6) {
            type(into: email, "ios.verify@example.com")
            let pw = app.secureTextFields.firstMatch
            if pw.waitForExistence(timeout: 4) { type(into: pw, "iosVerify-2026!") }
            if app.buttons["Continue"].exists {
                tapUntil(app.buttons["Continue"], name: "Continue") { self.app.tabBars.firstMatch.exists }
            }
        }
        _ = app.tabBars.firstMatch.waitForExistence(timeout: 30)
        sleep(5)
    }

    func testSettingsWorkspaceAndConnections() throws {
        signIn()

        let gear = app.buttons["Account and settings"]
        guard gear.waitForExistence(timeout: 8) else {
            miss("no Account and settings button"); return
        }
        var opened = false
        for _ in 0..<3 {
            gear.tap()
            if app.navigationBars["Settings"].waitForExistence(timeout: 8) { opened = true; break }
            sleep(2)
        }
        guard opened else { miss("Settings sheet did not open"); return }
        sleep(1)
        shot("settings-root")

        // --- Workspace ------------------------------------------------
        if tapRowByLabel("Workspace", waitFor: { self.app.navigationBars["Workspace"].exists }) {
            sleep(2)
            shot("workspace")

            // Prove real member data rendered rather than a plausible-
            // looking empty state — the signed-in account's own name must
            // appear, since the seeded workspace's owner is always a member
            // of it.
            if !app.staticTexts["Iris Vance"].waitForExistence(timeout: 5) {
                miss("member row 'Iris Vance' did not render")
            }

            // The Invite trigger — this screen's one accent action. Opened
            // to prove the form renders; cancelled rather than sent, since
            // this pass verifies rendering, not the write itself (already
            // covered by the decode tests against real captured responses).
            let inviteTrigger = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] 'Invite someone'")).firstMatch
            if inviteTrigger.waitForExistence(timeout: 5) {
                inviteTrigger.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                sleep(1)
                shot("workspace-invite-form")
                if app.buttons["Cancel"].exists {
                    app.buttons["Cancel"].coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                    sleep(1)
                }
            } else {
                miss("no 'Invite someone' trigger (is this account really the workspace owner?)")
            }

            back()
        }

        // --- Connections ------------------------------------------------
        if tapRowByLabel("Connections", waitFor: { self.app.navigationBars["Connections"].exists }) {
            sleep(2)
            shot("connections")
        }

        print("SETTINGS_DONE \(shotDir.path) misses=\(misses.count) \(misses)")
    }
}

import XCTest

/// TEMPORARY. The things the main walkthrough does not reach: a table-bearing
/// document, search results that actually open, a real write, and the
/// My work "Show done" toggle.
final class Supplementary: XCTestCase {

    private var app: XCUIApplication!
    private var shotDir: URL!
    private var step = 0
    private var misses: [String] = []

    override func setUpWithError() throws {
        continueAfterFailure = true
        let label = ProcessInfo.processInfo.environment["SHOT_LABEL"] ?? "supp"
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

    private func miss(_ s: String) { misses.append(s); print("SUPP_MISS \(s)") }
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
    private func tab(_ index: Int, expect navBar: String) -> Bool {
        let bar = app.tabBars.firstMatch
        guard bar.waitForExistence(timeout: 10), bar.buttons.count > index else { return false }
        for _ in 0..<3 {
            bar.buttons.element(boundBy: index).tap()
            if app.navigationBars[navBar].waitForExistence(timeout: 8) { sleep(2); return true }
            sleep(2)
        }
        miss("tab \(index) never reached '\(navBar)'")
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

    func testSupplementary() throws {
        signIn()

        // --- My work: the "Show done" toggle, and the agent bucket --------
        if tab(1, expect: "My work") {
            shot("mywork-open-only")
            let toggle = app.navigationBars.buttons["Show done"]
            if toggle.waitForExistence(timeout: 5) {
                toggle.tap(); sleep(2)
                shot("mywork-show-done")
                app.swipeUp(); usleep(800_000); shot("mywork-show-done-2")
                if app.navigationBars.buttons["Hide done"].exists {
                    app.navigationBars.buttons["Hide done"].tap(); sleep(1)
                    shot("mywork-hidden-again")
                } else { miss("toggle did not flip to 'Hide done'") }
            } else { miss("no 'Show done' toggle (no finished work assigned?)") }
        }

        // --- SUB-TASKS ----------------------------------------------------
        // The main walkthrough consistently loses this one: it is the last
        // row of the Mobile App list and the step before it leaves the list
        // scrolled. Opened FIRST here, from a clean navigation state.
        if tab(2, expect: "Projects") {
            if tapRowByLabel("Mobile App", waitFor: { self.app.navigationBars["Mobile App"].exists }) {
                if tapRowByLabel("Offline cache for the document reader",
                                 waitFor: { self.app.textFields["Comment"].exists }) {
                    sleep(2)
                    shot("task-subtasks")
                    app.swipeUp(); usleep(900_000); shot("task-subtasks-1")
                    app.swipeUp(); usleep(900_000); shot("task-subtasks-2")
                    back()
                }
                back()
            }
        }

        // --- AN AGENT WHOSE RUN ENDED IN AN ERROR -------------------------
        // The trace footer painted Theme.online (green) for BOTH a clean
        // finish and a run that ended in an error. This is the screen that
        // shows which tone it uses now.
        if tab(3, expect: "Agents") {
            shot("agents-list")
            if tapRowByLabel("Release Scout", waitFor: { self.app.navigationBars["Release Scout"].exists }) {
                sleep(6)
                shot("agent-run-ended-with-error")
                back()
            }
        }

        // --- A DOCUMENT CONTAINING A MARKDOWN TABLE -----------------------
        if tab(2, expect: "Projects") {
            if tapRowByLabel("Mobile App", waitFor: { self.app.navigationBars["Mobile App"].exists }) {
                let docs = app.segmentedControls.buttons["Documents"].exists
                    ? app.segmentedControls.buttons["Documents"]
                    : app.buttons["Documents"]
                if docs.waitForExistence(timeout: 5) {
                    docs.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                    sleep(3)
                    shot("documents-list")
                    if tapRowByLabel("iOS Release Checklist", waitFor: { self.app.staticTexts["specs/ios-release-checklist.md"].exists }) {
                        sleep(2)
                        shot("doc-table-top")
                        app.swipeUp(); usleep(900_000); shot("doc-table-1")
                        app.swipeUp(); usleep(900_000); shot("doc-table-2")
                        app.swipeUp(); usleep(900_000); shot("doc-table-3")
                        back()
                    }
                } else { miss("no Documents segment") }
                back()
            }
        }

        // --- SEARCH RESULTS MUST OPEN -------------------------------------
        if tab(4, expect: "Search") {
            let field = app.searchFields.firstMatch.exists ? app.searchFields.firstMatch : app.textFields.firstMatch
            if field.waitForExistence(timeout: 6) {
                type(into: field, "Polar")
                sleep(4)
                shot("search-results")
                // a TASK hit
                if tapRowByLabel("Create the Polar product", waitFor: { self.app.textFields["Comment"].exists }) {
                    shot("search-opened-task")
                    back()
                    sleep(2)
                }
                // a DOCUMENT hit
                if tapRowByLabel("Polar Integration Notes", waitFor: {
                    self.app.staticTexts["specs/polar-integration.md"].exists
                }) {
                    shot("search-opened-document")
                    app.swipeUp(); usleep(900_000); shot("search-opened-document-2")
                    back()
                }
            } else { miss("no search field") }
        }

        // --- A REAL WRITE: change a status, then change it back -----------
        if tab(2, expect: "Projects") {
            if tapRowByLabel("Infrastructure", waitFor: { self.app.navigationBars["Infrastructure"].exists }) {
                if tapRowByLabel("Nightly backup verification", waitFor: { self.app.textFields["Comment"].exists }) {
                    shot("write-before")
                    let statusRow = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] 'Status'")).firstMatch
                    if statusRow.waitForExistence(timeout: 5) {
                        statusRow.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                        sleep(2)
                        shot("write-status-sheet")
                        let inProgress = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] 'In progress'")).firstMatch
                        if inProgress.waitForExistence(timeout: 5) {
                            inProgress.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                            sleep(3)
                            shot("write-after")
                            // put it back
                            statusRow.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                            sleep(2)
                            let todo = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] 'Todo'")).firstMatch
                            if todo.waitForExistence(timeout: 5) {
                                todo.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                                sleep(3)
                                shot("write-reverted")
                            } else { miss("could not revert the status") }
                        } else { miss("no 'In progress' option in the status sheet") }
                    } else { miss("no Status row") }
                    back()
                }
                back()
            }
        }

        // --- SIGN OUT, capture the login screen, sign back in ------------
        //
        // `xcrun simctl erase` does NOT clear the simulator keychain, so an
        // "erased" device still restores its session and the walkthrough
        // never sees the login form. Driving the app's own Sign Out is both
        // the reliable way to reach that screen AND a test of the button.
        if tab(0, expect: "Inbox") {
            let gear = app.buttons["Account and settings"]
            if gear.waitForExistence(timeout: 8) {
                gear.tap()
                if app.navigationBars["Settings"].waitForExistence(timeout: 8) {
                    let signOut = app.buttons["Sign Out"]
                    if signOut.waitForExistence(timeout: 5) {
                        tapUntil(signOut, name: "Sign Out") { !self.app.tabBars.firstMatch.exists }
                        sleep(2)
                        shot("after-sign-out")
                        if app.tabBars.firstMatch.exists { miss("Sign Out left the tab bar on screen") }
                        // The welcome screen is once-per-install, so signing
                        // out lands straight on the login form.
                        if app.textFields.firstMatch.waitForExistence(timeout: 8) {
                            shot("login-screen")
                            let email = app.textFields.firstMatch
                            type(into: email, "ios.verify@example.com")
                            let pw = app.secureTextFields.firstMatch
                            if pw.waitForExistence(timeout: 4) { type(into: pw, "iosVerify-2026!") }
                            shot("login-filled")
                            if app.buttons["Continue"].exists { app.buttons["Continue"].tap() }
                            if app.tabBars.firstMatch.waitForExistence(timeout: 30) {
                                sleep(4)
                                shot("signed-back-in")
                            } else { miss("could not sign back in") }
                        } else { miss("no login form after signing out") }
                    } else { miss("no Sign Out button") }
                } else { miss("Settings did not open for sign-out") }
            }
        }

        print("SUPP_DONE \(shotDir.path) misses=\(misses.count) \(misses)")
    }
}

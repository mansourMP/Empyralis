import XCTest

/// TEMPORARY verification harness — not part of the shipped project. It drives
/// the real app against the real seeded backend and captures what every screen
/// actually renders.
///
/// Two rules learned the hard way here:
///   * tap tabs by INDEX. `tabBars.buttons["Projects"]` resolved, reported
///     hittable, and silently did not switch — so the screenshot named
///     "projects" was actually My work.
///   * after every navigation, ASSERT the destination. A harness that
///     screenshots whatever is on screen will happily photograph the previous
///     screen and call it verified.
final class Walkthrough: XCTestCase {

    private var app: XCUIApplication!
    private var shotDir: URL!
    private var step = 0
    private var misses: [String] = []

    override func setUpWithError() throws {
        continueAfterFailure = true
        let label = ProcessInfo.processInfo.environment["SHOT_LABEL"] ?? "run"
        let base = URL(fileURLWithPath: "/tmp/emp-shots").appendingPathComponent(label)
        try? FileManager.default.removeItem(at: base)
        try? FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        shotDir = base
        app = XCUIApplication()
    }

    private func shot(_ name: String) {
        step += 1
        let png = XCUIScreen.main.screenshot().pngRepresentation
        let file = shotDir.appendingPathComponent(String(format: "%02d-%@.png", step, name))
        try? png.write(to: file)
        print("SHOT \(file.path)")
    }

    private func miss(_ s: String) { misses.append(s); print("WALK_MISS \(s)") }

    @discardableResult
    private func tab(_ index: Int, expect navBar: String) -> Bool {
        let bar = app.tabBars.firstMatch
        guard bar.waitForExistence(timeout: 10), bar.buttons.count > index else {
            miss("tab bar missing for index \(index)"); return false
        }
        for attempt in 0..<3 {
            bar.buttons.element(boundBy: index).tap()
            if app.navigationBars[navBar].waitForExistence(timeout: 8) { sleep(2); return true }
            print("WALK_RETRY tab \(index) attempt \(attempt)")
            sleep(2)
        }
        miss("tab \(index) did not reach '\(navBar)'")
        return false
    }

    /// Rows are Buttons inside Cells; section headers are Cells with no
    /// Button, so querying buttons skips them for free.
    private func rows() -> XCUIElementQuery { app.collectionViews.buttons }

    /// Find a row, tap it, and PROVE it navigated.
    ///
    /// Two things this had to learn. XCUITest will not scroll for you, so a
    /// row below the fold reads exactly like a row that is not there. And a
    /// `.tap()` on a SwiftUI NavigationLink row lands but does not always
    /// activate the link — the same silent no-op the tab buttons had. A
    /// coordinate tap on the row's centre does activate it, and waiting for
    /// the destination is what turns "it didn't work" from a screenshot of
    /// the wrong screen into a reported miss.
    @discardableResult
    private func tapRow(_ text: String, expect navBar: String? = nil, timeout: TimeInterval = 8) -> Bool {
        let e = rows().matching(NSPredicate(format: "label CONTAINS[c] %@", text)).firstMatch

        func present() -> Bool { e.exists && e.isHittable }

        if !e.waitForExistence(timeout: timeout) || !present() {
            for _ in 0..<4 { app.swipeDown() }
            usleep(500_000)
            var found = false
            for _ in 0..<8 {
                if present() { found = true; break }
                app.swipeUp(); usleep(400_000)
            }
            if !found && !e.exists { miss("no row '\(text)'"); return false }
        }

        for attempt in 0..<3 {
            e.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            guard let navBar else { sleep(2); return true }
            if app.navigationBars[navBar].waitForExistence(timeout: 8) { sleep(1); return true }
            print("WALK_RETRY row '\(text)' attempt \(attempt)")
            sleep(1)
        }
        miss("row '\(text)' never reached '\(navBar ?? "")'")
        return false
    }

    /// Task detail has NO navigation title (the body owns it), so the proof
    /// of arrival is the comment composer, which exists nowhere else.
    @discardableResult
    private func tapTaskRow(_ text: String) -> Bool {
        let e = rows().matching(NSPredicate(format: "label CONTAINS[c] %@", text)).firstMatch
        if !e.waitForExistence(timeout: 8) || !e.isHittable {
            for _ in 0..<4 { app.swipeDown() }
            usleep(500_000)
            var found = false
            for _ in 0..<8 {
                if e.exists && e.isHittable { found = true; break }
                app.swipeUp(); usleep(400_000)
            }
            if !found && !e.exists { miss("no task row '\(text)'"); return false }
        }
        for attempt in 0..<3 {
            e.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            if app.textFields["Comment"].waitForExistence(timeout: 8) { sleep(2); return true }
            print("WALK_RETRY task '\(text)' attempt \(attempt)")
            sleep(1)
        }
        miss("task '\(text)' never opened")
        return false
    }

    /// The project's Tasks/Documents picker is a segmented control; its
    /// options are not always reachable as plain `app.buttons`.
    @discardableResult
    private func segment(_ title: String) -> Bool {
        let seg = app.segmentedControls.buttons[title]
        if seg.waitForExistence(timeout: 5), seg.isHittable {
            seg.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            sleep(2); return true
        }
        let plain = app.buttons[title]
        if plain.waitForExistence(timeout: 4) {
            plain.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            sleep(2); return true
        }
        miss("no '\(title)' segment")
        return false
    }

    /// Tap, then WAIT FOR THE CONSEQUENCE, then tap again if it did not
    /// happen. Every silent no-op in this harness has been a SwiftUI control
    /// that resolved and reported hittable while `.tap()` changed nothing.
    @discardableResult
    private func tapUntil(_ e: XCUIElement, name: String, timeout: TimeInterval = 8,
                          _ condition: @escaping () -> Bool) -> Bool {
        for attempt in 0..<4 {
            if condition() { return true }
            e.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            let deadline = Date().addingTimeInterval(timeout)
            while Date() < deadline {
                if condition() { sleep(1); return true }
                usleep(300_000)
            }
            print("WALK_RETRY tap '\(name)' attempt \(attempt)")
        }
        miss("tapping '\(name)' never produced its result")
        return false
    }

    private func back() {
        let b = app.navigationBars.buttons.element(boundBy: 0)
        if b.exists { b.tap(); sleep(2) }
    }

    /// Tapping a field does not always give it keyboard focus on the first
    /// try — `typeText` then aborts the whole test with "Neither element nor
    /// any descendant has keyboard focus". Retry rather than lose the run.
    /// `app.keyboards.count > 0` is NOT a focus check — the previous field's
    /// keyboard is still up, so it reports true while the field you just
    /// tapped has no focus, and `typeText` then aborts the whole run with
    /// "Neither element nor any descendant has keyboard focus". Ask the
    /// ELEMENT whether it has focus.
    private func hasFocus(_ e: XCUIElement) -> Bool {
        (e.value(forKey: "hasKeyboardFocus") as? Bool) ?? false
    }

    private func type(into field: XCUIElement, _ text: String) {
        for attempt in 0..<4 {
            if !hasFocus(field) {
                field.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                usleep(700_000)
            }
            if hasFocus(field) {
                field.typeText(text)
                return
            }
            print("WALK_RETRY focus attempt \(attempt)")
            sleep(1)
        }
        miss("could not focus a text field to type into")
    }

    /// Fixed number of viewport steps. An earlier version compared
    /// `app.debugDescription` between swipes to detect the bottom — that is
    /// a full accessibility snapshot of the whole app and cost several
    /// seconds EACH, which is what made a single device take longer than the
    /// harness had.
    private func scrollShots(_ name: String, max: Int = 3) {
        shot(name)
        for i in 1...max {
            app.swipeUp(); usleep(700_000)
            shot("\(name)-\(i)")
        }
    }

    func testWalk() throws {
        app.launch()
        sleep(2)

        // --- Welcome + Login ------------------------------------------------
        //
        // Every tap here is a COORDINATE tap with a retry that waits for the
        // destination. A plain `.tap()` on "Get started" resolved, reported
        // hittable, and left the welcome screen on screen — the same silent
        // no-op the tab buttons and the list rows both had.
        //
        // Note `simctl erase` does NOT clear the simulator keychain, so a
        // device that was signed in before may restore its session and skip
        // this whole block. That is handled, not assumed.
        let getStarted = app.buttons["Get started"]
        if getStarted.waitForExistence(timeout: 8) {
            shot("welcome")
            tapUntil(getStarted, name: "Get started") {
                self.app.textFields.firstMatch.exists || self.app.tabBars.firstMatch.exists
            }
        }

        let email = app.textFields.firstMatch
        if email.waitForExistence(timeout: 6) {
            shot("login-empty")
            type(into: email, "ios.verify@example.com")
            let pw = app.secureTextFields.firstMatch
            if pw.waitForExistence(timeout: 4) { type(into: pw, "iosVerify-2026!") }
            shot("login-filled-keyboard")
            let cont = app.buttons["Continue"]
            if cont.exists {
                tapUntil(cont, name: "Continue") { self.app.tabBars.firstMatch.exists }
            } else { miss("no Continue button") }
        } else if app.tabBars.firstMatch.exists {
            print("WALK_NOTE session restored from the keychain — login skipped")
        } else {
            miss("login form never appeared")
        }

        XCTAssertTrue(app.tabBars.firstMatch.waitForExistence(timeout: 30), "sign-in failed")
        sleep(5)

        // --- Inbox ---------------------------------------------------------
        shot("inbox")

        let gear = app.buttons["Account and settings"]
        if gear.waitForExistence(timeout: 8) {
            var opened = false
            for _ in 0..<3 {
                gear.tap()
                if app.navigationBars["Settings"].waitForExistence(timeout: 8) { opened = true; break }
                sleep(2)
            }
            if opened { sleep(1); shot("settings") } else { miss("Settings sheet did not open") }
            if app.buttons["Done"].exists { app.buttons["Done"].tap() } else { app.swipeDown() }
            sleep(2)
        } else { miss("no Account and settings button") }

        // --- My work -------------------------------------------------------
        if tab(1, expect: "My work") { scrollShots("mywork", max: 2) }

        // --- Projects ------------------------------------------------------
        if tab(2, expect: "Projects") {
            shot("projects")

            if tapRow("Mobile App", expect: "Mobile App") {
                sleep(2)
                shot("project-tasks")
                app.swipeUp(); usleep(900_000); shot("project-tasks-bottom")
                for _ in 0..<4 { app.swipeDown() }; usleep(700_000)

                // ---- Documents inside the project ----
                if segment("Documents") {
                    sleep(2)
                    shot("project-documents")
                    let firstDoc = rows().element(boundBy: 0)
                    if firstDoc.waitForExistence(timeout: 8) {
                        firstDoc.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                        sleep(3)
                        scrollShots("document", max: 3)
                        back()
                    } else { miss("no document row") }
                    segment("Tasks")
                }

                // ---- A RICH task: description + 2 labels + due + 2 comments
                if tapTaskRow("Sign-in screen loses the keyboard") {
                    sleep(2)
                    scrollShots("task-rich", max: 4)

                    // composer with the keyboard up — the one thing a
                    // screenshot of a still screen cannot tell you
                    let composer = app.textFields["Comment"]
                    if composer.waitForExistence(timeout: 5) {
                        composer.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                        sleep(2)
                        shot("task-composer-keyboard")
                        if hasFocus(composer) { composer.typeText("Checked on device") }
                        sleep(1)
                        shot("task-composer-typed")
                        // dismiss without sending
                        app.swipeDown(); sleep(1)
                    } else { miss("no comment composer") }

                    // property sheets
                    if app.buttons.matching(NSPredicate(format: "label CONTAINS[c] 'Status'")).firstMatch.exists {
                        app.buttons.matching(NSPredicate(format: "label CONTAINS[c] 'Status'")).firstMatch.tap()
                        sleep(2); shot("task-status-sheet")
                        // The picker sheet's dismiss is "Done" — there is no
                        // Cancel. Looking for the wrong label left the sheet
                        // up and stranded every step after it.
                        if app.buttons["Done"].waitForExistence(timeout: 3) {
                            tapUntil(app.buttons["Done"], name: "Done") {
                                !self.app.buttons["Backlog"].exists
                            }
                        } else { app.swipeDown() }
                        sleep(1)
                    } else { miss("no Status row to open") }
                    back()
                }

                // ---- A task with SUB-TASKS ----
                if tapTaskRow("Offline cache for the document reader") {
                    sleep(2)
                    scrollShots("task-subtasks", max: 3)
                    back()
                }
                back()
            }
        }

        // --- Agents --------------------------------------------------------
        if tab(3, expect: "Agents") {
            shot("agents")
            let firstAgent = rows().element(boundBy: 0)
            if firstAgent.waitForExistence(timeout: 8) {
                firstAgent.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                sleep(6)
                scrollShots("agent", max: 3)
                back()
            } else { miss("no agent row to open") }
        }

        // --- Search --------------------------------------------------------
        if tab(4, expect: "Search") {
            shot("search-empty")
            let field = app.searchFields.firstMatch.exists ? app.searchFields.firstMatch : app.textFields.firstMatch
            if field.waitForExistence(timeout: 6) {
                type(into: field, "Polar")
                sleep(4)
                shot("search-polar-keyboard")
                app.swipeUp(); usleep(1_000_000)
                shot("search-polar-scrolled")
            } else { miss("no search field") }
        }

        print("WALK_DONE \(shotDir.path) misses=\(misses.count) \(misses)")
    }
}

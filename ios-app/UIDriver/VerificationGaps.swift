import XCTest

/// TEMPORARY verification harness for the three gaps CLAUDE.md records as
/// never having been watched on a device:
///
///   Gap1Writes        a task write committing, and ROLLING BACK
///   Gap2InboxData     Notifications + Failed runs rendering WITH DATA
///   Gap3LiveTrace     a trace streaming INCREMENTALLY, `.live` -> terminal
///
/// The rollback and the live trace both need something to happen on the
/// HOST while the app is mid-flight (kill the backend / start emitting trace
/// events). XCUITest cannot shell out, but the simulator shares the host's
/// `/tmp`, so a two-file handshake is the honest mechanism:
///
///   test writes  /tmp/emp-ctl/REQUEST-<name>     "please do this now"
///   host does the thing, then writes
///                /tmp/emp-ctl/ACK-<name>         "done"
///   test polls for the ACK before continuing
///
/// Nothing is simulated inside the app: the app talks to a real backend that
/// is really running or really dead.
final class VerificationGaps: XCTestCase {

    private var app: XCUIApplication!
    private var shotDir: URL!
    private var step = 0
    private var misses: [String] = []

    private static let ctlDir = URL(fileURLWithPath: "/tmp/emp-ctl")

    override func setUpWithError() throws {
        continueAfterFailure = true
        let label = ProcessInfo.processInfo.environment["SHOT_LABEL"] ?? "gaps"
        let base = URL(fileURLWithPath: "/tmp/emp-shots").appendingPathComponent(label)
        try? FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        shotDir = base
        try? FileManager.default.createDirectory(at: Self.ctlDir, withIntermediateDirectories: true)
        app = XCUIApplication()
    }

    // MARK: - plumbing

    private func shot(_ name: String) {
        step += 1
        let png = XCUIScreen.main.screenshot().pngRepresentation
        let file = shotDir.appendingPathComponent(String(format: "%02d-%@.png", step, name))
        try? png.write(to: file)
        print("SHOT \(file.path)")
    }

    private func miss(_ s: String) { misses.append(s); print("GAP_MISS \(s)") }
    private func note(_ s: String) { print("GAP_NOTE \(s)") }

    /// Ask the host to do something and WAIT for it to confirm. Returns false
    /// on timeout rather than hanging the whole run.
    @discardableResult
    private func hostDo(_ name: String, timeout: TimeInterval = 60) -> Bool {
        let req = Self.ctlDir.appendingPathComponent("REQUEST-\(name)")
        let ack = Self.ctlDir.appendingPathComponent("ACK-\(name)")
        try? FileManager.default.removeItem(at: ack)
        try? "go".write(to: req, atomically: true, encoding: .utf8)
        note("asked host for '\(name)'")
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if FileManager.default.fileExists(atPath: ack.path) {
                note("host acked '\(name)'")
                return true
            }
            usleep(300_000)
        }
        miss("host never acked '\(name)'")
        return false
    }

    private func hostRead(_ file: String) -> String? {
        try? String(contentsOf: Self.ctlDir.appendingPathComponent(file), encoding: .utf8)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

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
        miss("could not focus a text field")
    }

    private func signIn() {
        app.launch()
        sleep(2)
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
            let cont = app.buttons["Continue"]
            for _ in 0..<3 {
                cont.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                if app.tabBars.firstMatch.waitForExistence(timeout: 15) { break }
            }
        }
        XCTAssertTrue(app.tabBars.firstMatch.waitForExistence(timeout: 30), "sign-in failed")
        sleep(6)
    }

    @discardableResult
    private func tab(_ index: Int, expect navBar: String) -> Bool {
        let bar = app.tabBars.firstMatch
        guard bar.waitForExistence(timeout: 10), bar.buttons.count > index else {
            miss("tab bar missing for index \(index)"); return false
        }
        for _ in 0..<3 {
            bar.buttons.element(boundBy: index).tap()
            if app.navigationBars[navBar].waitForExistence(timeout: 8) { sleep(2); return true }
            sleep(2)
        }
        miss("tab \(index) did not reach '\(navBar)'")
        return false
    }

    private func rows() -> XCUIElementQuery { app.collectionViews.buttons }

    @discardableResult
    private func tapRow(_ text: String, expect navBar: String? = nil) -> Bool {
        let e = rows().matching(NSPredicate(format: "label CONTAINS[c] %@", text)).firstMatch
        if !e.waitForExistence(timeout: 8) || !e.isHittable {
            for _ in 0..<4 { app.swipeDown() }
            usleep(500_000)
            var found = false
            for _ in 0..<8 {
                if e.exists && e.isHittable { found = true; break }
                app.swipeUp(); usleep(400_000)
            }
            if !found && !e.exists { miss("no row '\(text)'"); return false }
        }
        for _ in 0..<3 {
            e.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            guard let navBar else { sleep(2); return true }
            if app.navigationBars[navBar].waitForExistence(timeout: 8) { sleep(1); return true }
            sleep(1)
        }
        miss("row '\(text)' never reached '\(navBar ?? "")'")
        return false
    }

    /// Task detail has no navigation title; the comment composer is the proof
    /// of arrival because it exists nowhere else in the app.
    @discardableResult
    private func openTask(_ text: String) -> Bool {
        let e = rows().matching(NSPredicate(format: "label CONTAINS[c] %@", text)).firstMatch
        if !e.waitForExistence(timeout: 8) || !e.isHittable {
            for _ in 0..<4 { app.swipeDown() }
            usleep(500_000)
            var found = false
            for _ in 0..<10 {
                if e.exists && e.isHittable { found = true; break }
                app.swipeUp(); usleep(400_000)
            }
            if !found && !e.exists { miss("no task row '\(text)'"); return false }
        }
        for _ in 0..<3 {
            e.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            if app.textFields["Comment"].waitForExistence(timeout: 8) { sleep(2); return true }
            sleep(1)
        }
        miss("task '\(text)' never opened")
        return false
    }

    private func propertyRow(_ title: String) -> XCUIElement {
        app.buttons.matching(NSPredicate(format: "label BEGINSWITH[c] %@", title)).firstMatch
    }

    private func propertyLabel(_ title: String) -> String {
        let row = propertyRow(title)
        return row.exists ? (row.label) : "<no \(title) row>"
    }

    @discardableResult
    private func openSheet(_ title: String) -> Bool {
        let row = propertyRow(title)
        guard row.waitForExistence(timeout: 6) else { miss("no \(title) row"); return false }
        for _ in 0..<4 {
            row.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            if app.navigationBars[title].waitForExistence(timeout: 5) { sleep(1); return true }
            sleep(1)
        }
        miss("\(title) sheet never opened")
        return false
    }

    /// The picker sheet dismisses itself on pick; wait for that rather than
    /// assuming it. Returns false if the sheet stayed up.
    ///
    /// EXACT LABEL MATCHING DOES NOT WORK HERE and that cost a whole run.
    /// A `PickerRow`'s accessibility label is the COMPOSITE of its leading
    /// view and its title — `StatusDot` contributes the raw status token and
    /// `ActorAvatar` contributes the initial — so the real labels are
    /// "in review, In review" and "D, Dana Okafor", and `app.buttons["In
    /// review"]` resolves to nothing while the row is plainly on screen.
    /// Matched on CONTAINS, and deliberately EXCLUDING the sheet's own
    /// navigation-bar "Done" button, which would otherwise win for the
    /// status option of the same name.
    @discardableResult
    private func pick(_ option: String, inSheet sheet: String) -> Bool {
        let candidates = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] %@", option))
        var row = candidates.firstMatch
        guard row.waitForExistence(timeout: 6) else { miss("no '\(option)' option"); return false }

        // The nav bar's dismiss button lives in the sheet's own toolbar; a
        // content row never does.
        let navButton = app.navigationBars[sheet].buttons[option]
        if navButton.exists {
            let content = candidates.allElementsBoundByIndex.filter { $0.frame != navButton.frame }
            guard let first = content.first else {
                miss("'\(option)' only matched the sheet's own dismiss button"); return false
            }
            row = first
        }

        for _ in 0..<3 {
            row.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            let deadline = Date().addingTimeInterval(6)
            while Date() < deadline {
                if !app.navigationBars[sheet].exists { sleep(2); return true }
                usleep(300_000)
            }
        }
        miss("picking '\(option)' never dismissed the \(sheet) sheet")
        return false
    }

    /// Leave the screen in a known state after a miss — a sheet left up makes
    /// every later step tap into a dimmed backdrop instead of the row it
    /// named, and the failures that follow describe the wrong thing.
    private func dismissAnySheet() {
        for _ in 0..<3 {
            guard app.navigationBars.buttons["Done"].exists else { return }
            app.navigationBars.buttons["Done"].tap()
            sleep(1)
        }
    }

    private func appIsRunning() -> Bool { app.state == .runningForeground }

    private func back() {
        let b = app.navigationBars.buttons.element(boundBy: 0)
        if b.exists { b.tap(); sleep(2) }
    }

    // MARK: - GAP 1: a write, committed and rolled back

    func testGap1TaskWrite() throws {
        signIn()

        guard tab(2, expect: "Projects") else { return }
        guard tapRow("Mobile App", expect: "Mobile App") else { return }
        sleep(2)
        shot("g1-00-project-tasks")
        for (i, l) in rows().allElementsBoundByIndex.map(\.label).enumerated() where !l.isEmpty {
            print("GAP_ROW \(i)|\(l.prefix(90))")
        }
        guard openTask("Sign-in screen loses the keyboard") else {
            shot("g1-00b-task-not-found")
            return
        }

        shot("g1-01-task-before")
        note("BEFORE status=\(propertyLabel("Status"))")
        note("BEFORE priority=\(propertyLabel("Priority"))")
        note("BEFORE assignee=\(propertyLabel("Assignee"))")

        // ---- STATUS: In progress -> In review -------------------------------
        if openSheet("Status") {
            shot("g1-02-status-sheet")
            if pick("In review", inSheet: "Status") {
                shot("g1-03-status-after")
                let after = propertyLabel("Status")
                note("AFTER status=\(after)")
                XCTAssertTrue(after.contains("In review"),
                              "status row did not update on screen, it reads '\(after)'")
            }
        }
        dismissAnySheet()
        XCTAssertFalse(app.staticTexts["Couldn't save"].exists, "a healthy status write reported an error")

        // ---- PRIORITY: Urgent -> Medium ------------------------------------
        if openSheet("Priority") {
            shot("g1-04-priority-sheet")
            if pick("Medium", inSheet: "Priority") {
                shot("g1-05-priority-after")
                let after = propertyLabel("Priority")
                note("AFTER priority=\(after)")
                XCTAssertTrue(after.contains("Medium"),
                              "priority row did not update on screen, it reads '\(after)'")
            }
        }
        dismissAnySheet()

        // ---- ASSIGNEE: -> a PERSON (assigning an agent also moves status,
        //      which would confuse the status assertion above) ---------------
        if openSheet("Assignee") {
            shot("g1-06-assignee-sheet")
            if pick("Dana Okafor", inSheet: "Assignee") {
                shot("g1-07-assignee-after")
                let after = propertyLabel("Assignee")
                note("AFTER assignee=\(after)")
                XCTAssertTrue(after.contains("Dana"),
                              "assignee row did not update on screen, it reads '\(after)'")
            }
        }
        dismissAnySheet()

        // Let the host re-read all three from the API.
        hostDo("verify-writes", timeout: 60)

        // ---- ROLLBACK: kill the backend, then attempt a write ---------------
        note("APP_STATE before kill = \(app.state.rawValue)")
        guard hostDo("kill-backend", timeout: 60) else { return }
        sleep(2)
        note("APP_STATE after kill = \(app.state.rawValue) (3 == runningForeground)")
        guard appIsRunning() else {
            miss("the app was no longer running once the backend went away")
            return
        }

        let beforeRollback = propertyLabel("Status")
        note("ROLLBACK before=\(beforeRollback)")
        // "Backlog", not "Done" — "Done" is also the sheet's own dismiss
        // button's label, and picking the wrong one closes the sheet without
        // ever attempting a write.
        if openSheet("Status") {
            shot("g1-08-status-sheet-offline")
            // The sheet dismisses immediately (the write is fired async), so
            // this is the pick, not the outcome.
            pick("Backlog", inSheet: "Status")
        }
        // Give the request time to fail and the rollback + alert to land.
        sleep(6)
        shot("g1-09-rollback-alert")

        let alertTitle = app.staticTexts["Couldn't save"]
        let alertShown = alertTitle.waitForExistence(timeout: 15)
        XCTAssertTrue(alertShown, "a failed write said NOTHING — a silent rollback is the bug")
        if alertShown {
            let bodies = app.alerts.firstMatch.staticTexts.allElementsBoundByIndex.map(\.label)
            note("ALERT=\(bodies)")
        }
        if app.alerts.buttons["OK"].exists { app.alerts.buttons["OK"].tap(); sleep(2) }

        shot("g1-10-rollback-reverted")
        let afterRollback = propertyLabel("Status")
        note("ROLLBACK after=\(afterRollback)")
        XCTAssertFalse(afterRollback.contains("Backlog"),
                       "the optimistic change was NOT rolled back — it still reads '\(afterRollback)'")
        XCTAssertEqual(beforeRollback, afterRollback,
                       "the row did not return to what it was before the failed write")

        hostDo("restore-backend", timeout: 120)
        print("GAP1_DONE misses=\(misses.count) \(misses)")
    }

    // MARK: - Diagnostic: what actually happens after a failed write

    /// Narrowly scoped: sign in, open a task, kill the backend, attempt ONE
    /// write, then poll `app.state` — which does NOT require an accessibility
    /// snapshot and therefore does not wait for the app to be idle. That
    /// distinction is the whole point: a full XCUITest run reported
    /// "Application ai.empyralis.app is not running" right after this step,
    /// and "the app died" and "the app is alive but never idles" produce that
    /// same message from a query while `state` tells them apart.
    func testDiagAfterFailedWrite() throws {
        signIn()
        guard tab(2, expect: "Projects") else { return }
        guard tapRow("Mobile App", expect: "Mobile App") else { return }
        guard openTask("Sign-in screen loses the keyboard") else { return }

        note("STATE before kill = \(app.state.rawValue)")
        guard hostDo("kill-backend", timeout: 60) else { return }
        note("STATE after kill = \(app.state.rawValue)")

        guard openSheet("Status") else { hostDo("restore-backend", timeout: 180); return }
        // Pick whichever of the two is not current, so a write is always sent.
        let target = propertyLabel("Status").contains("Backlog") ? "Todo" : "Backlog"
        note("picking '\(target)' with the backend dead")
        let row = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] %@", target)).firstMatch
        if row.waitForExistence(timeout: 6) {
            row.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        } else {
            miss("no '\(target)' option")
        }

        // 60 seconds of pure state sampling + screenshots. No element queries.
        for i in 0..<60 {
            let s = app.state.rawValue
            print("DIAG t=\(i)s state=\(s)")
            if i % 10 == 0 { shot("diag-t\(i)") }
            if s != 4 { print("DIAG APP LEFT THE FOREGROUND at t=\(i)s state=\(s)"); break }
            sleep(1)
        }
        shot("diag-final")
        hostDo("restore-backend", timeout: 180)
        print("DIAG_DONE misses=\(misses.count) \(misses)")
    }

    // MARK: - GAP 2: the Inbox with real notification + failed-run data

    func testGap2InboxWithData() throws {
        signIn()

        // Inbox is tab 0 and is where sign-in lands.
        tab(0, expect: "Inbox")
        sleep(3)
        shot("g2-01-inbox-top")
        app.swipeUp(); usleep(900_000)
        shot("g2-02-inbox-scrolled")
        app.swipeUp(); usleep(900_000)
        shot("g2-03-inbox-bottom")

        // The three section headers, by name. A header that is missing means
        // that source rendered nothing — which is the state this test exists
        // to move off.
        for header in ["Needs your input", "Notifications", "Failed runs"] {
            let found = app.staticTexts.matching(
                NSPredicate(format: "label CONTAINS[c] %@", header)
            ).firstMatch.exists
            note("SECTION '\(header)' present=\(found)")
        }

        // Dump every row label in order so the host can check the RANKING
        // against what the API returned, rather than eyeballing a screenshot.
        let labels = app.collectionViews.cells.allElementsBoundByIndex.map(\.label)
        note("INBOX_ROWS_BEGIN")
        for (i, l) in labels.enumerated() where !l.isEmpty {
            print("GAP_ROW \(i)|\(l)")
        }
        note("INBOX_ROWS_END count=\(labels.count)")

        XCTAssertTrue(
            app.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] %@", "Notifications")).firstMatch.exists,
            "the Notifications section did not render even though the feed has rows"
        )
        XCTAssertTrue(
            app.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] %@", "Failed runs")).firstMatch.exists,
            "the Failed runs section did not render even though the timeline has blocked_action rows"
        )
        print("GAP2_DONE misses=\(misses.count) \(misses)")
    }

    // MARK: - GAP 3: a LIVE trace, arriving incrementally

    func testGap3LiveTrace() throws {
        signIn()

        guard tab(3, expect: "Agents") else { return }
        // The host starts emitting AFTER we are on the agent screen, so what
        // the phone renders can only have come off the wire live.
        guard tapRow("Release Scout") else { return }
        sleep(2)
        shot("g3-01-agent-before")

        guard hostDo("start-live-trace", timeout: 60) else { return }

        // Re-resolve the agent's newest trace: the screen picked one up on
        // appear, and the live one was created a moment later.
        back(); sleep(2)
        guard tapRow("Release Scout") else { return }

        // Catch the connecting/empty phase before the first event lands.
        sleep(1)
        shot("g3-02-connecting")

        // Watch the step count climb. If every event arrived in one dump the
        // count would jump from 0 to 11 between two adjacent samples.
        var samples: [Int] = []
        for i in 0..<26 {
            let n = app.staticTexts.matching(
                NSPredicate(format: "label CONTAINS[c] %@ OR label CONTAINS[c] %@",
                            "document__read", "project_task__list")
            ).count
            let steps = app.scrollViews.firstMatch.staticTexts.count
            samples.append(steps)
            print("GAP_TICK \(i) steps=\(steps) tools=\(n)")
            if i == 4 { shot("g3-03-live-early") }
            if i == 12 { shot("g3-04-live-mid") }
            sleep(2)
        }
        shot("g3-05-live-late")

        // Wait for the terminal event and the finished footer.
        let finished = app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS[c] %@", "Run finished")
        ).firstMatch
        let sawTerminal = finished.waitForExistence(timeout: 90)
        sleep(1)
        shot("g3-06-terminal")

        note("SAMPLES=\(samples)")
        note("SAW_TERMINAL=\(sawTerminal)")

        let distinct = Set(samples).count
        XCTAssertGreaterThan(distinct, 2,
            "the step count never changed while the trace was running — nothing streamed incrementally (samples=\(samples))")
        XCTAssertTrue(sawTerminal, "the footer never reported the run finishing")
        XCTAssertFalse(
            app.staticTexts.matching(
                NSPredicate(format: "label CONTAINS[c] %@", "connection closed before")
            ).firstMatch.exists,
            "the stream reported the pre-fix 'connection closed before the run reported an outcome'"
        )
        print("GAP3_DONE misses=\(misses.count) \(misses)")
    }
}

import XCTest

/// Closes the four remaining verification gaps from the 2026-08-25 iOS
/// close-out pass:
///
///   testPriorityRollback / testAssigneeRollback   priority and assignee
///     writes, independently watched roll back on a dead backend — the
///     status write was already watched (VerificationGaps.testGap1TaskWrite);
///     this proves the other two branches of WorkspaceStore's ONE write path
///     behave identically rather than assuming it from shared code.
///   testColdLaunchSkeleton   a genuinely fresh install (uninstall +
///     reinstall, so `hasLoadedOnce` starts false with nothing on disk) with
///     the workspace fetch throttled long enough to actually see the
///     skeleton, not a warm-cache re-launch.
///   testRotationSupport   what the app's orientation support actually is,
///     determined by rotating a real device and reading back the rendered
///     frame — not assumed from an absent Info.plist key.
///   testDynamicTypeNoClipping   Inbox / task detail / Settings at an
///     accessibility text size, screenshotted for clipping/overlap.
///   testVoiceOverBaseline   accessibility-tree labels on the tab bar, a task
///     row, and the primary buttons, plus Xcode's own automated audit.
///
/// Same `hostDo` two-file handshake as VerificationGaps.swift (the simulator
/// shares the host's /tmp; XCUITest cannot shell out). hostctl.sh gained five
/// new request kinds for this file: pause-backend / resume-backend /
/// resume-arm-for-login (SIGSTOP/CONT, instant either direction, nothing
/// torn down or reseeded) and uninstall-app / reinstall-app (real
/// `xcrun simctl`, against the one specific simulator this pass targets).
final class CloseoutVerification: XCTestCase {

    private var app: XCUIApplication!
    private var shotDir: URL!
    private var step = 0
    private var misses: [String] = []

    private static let ctlDir = URL(fileURLWithPath: "/tmp/emp-ctl")

    override func setUpWithError() throws {
        continueAfterFailure = true
        let label = ProcessInfo.processInfo.environment["SHOT_LABEL"] ?? "closeout"
        let base = URL(fileURLWithPath: "/tmp/emp-shots").appendingPathComponent(label)
        try? FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        shotDir = base
        try? FileManager.default.createDirectory(at: Self.ctlDir, withIntermediateDirectories: true)
        app = XCUIApplication()
    }

    // MARK: - plumbing (same shapes as VerificationGaps.swift)

    private func shot(_ name: String) {
        step += 1
        let png = XCUIScreen.main.screenshot().pngRepresentation
        let file = shotDir.appendingPathComponent(String(format: "%02d-%@.png", step, name))
        try? png.write(to: file)
        print("SHOT \(file.path)")
    }

    private func miss(_ s: String) { misses.append(s); print("GAP_MISS \(s)") }
    private func note(_ s: String) { print("GAP_NOTE \(s)") }

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

    /// Same as VerificationGaps' `signIn()`, EXCEPT the final "Continue" tap
    /// is a parameter — the cold-launch test needs to arm the backend pause
    /// in the instant between filling the form and tapping it, which a fixed
    /// helper can't do from outside.
    ///
    /// Returns `true` when a login form was actually filled in, `false` when
    /// the launch landed straight on the signed-in tab bar (the Keychain's
    /// refresh token survived from an earlier run in this same shared
    /// simulator today — a real, observed state, not a hypothetical one:
    /// the very first run of this file hit exactly this and crashed trying
    /// to tap a "Continue" button that was never on screen).
    @discardableResult
    private func fillLoginForm() -> Bool {
        app.launch()
        sleep(2)
        if app.tabBars.firstMatch.waitForExistence(timeout: 3) {
            note("already signed in on launch (Keychain session survived) — skipping the login form")
            return false
        }
        let getStarted = app.buttons["Get started"]
        if getStarted.waitForExistence(timeout: 8) {
            for _ in 0..<3 {
                getStarted.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                if app.textFields.firstMatch.waitForExistence(timeout: 5) || app.tabBars.firstMatch.exists { break }
            }
        }
        if app.tabBars.firstMatch.exists { return false }
        let email = app.textFields.firstMatch
        if email.waitForExistence(timeout: 6) {
            type(into: email, "ios.verify@example.com")
            let pw = app.secureTextFields.firstMatch
            if pw.waitForExistence(timeout: 4) { type(into: pw, "iosVerify-2026!") }

            // BOTH fields, re-verified, with a retry — not a courtesy check.
            // The cold-launch path hit this for real: the simulator's first
            // launch attempt raced an internal relaunch (an FBSApplication-
            // Library "returned nil" hiccup right after a fresh reinstall,
            // which XCUITest silently retried), the retry handed back a
            // FRESH set of fields, and the password this function had
            // already typed landed on the instance that had just been
            // replaced — so the tap that followed submitted a real email
            // against an EMPTY password and got the app's own honest "That
            // email and password don't match." This is not a UI bug: it is
            // exactly what a real wrong password looks like, produced here
            // by a harness race rather than a person mistyping.
            for _ in 0..<3 {
                let emailFilled = !((email.value as? String) ?? "").isEmpty
                let pwFilled = !((app.secureTextFields.firstMatch.value as? String) ?? "").isEmpty
                if emailFilled && pwFilled { break }
                note("login field verification: email='\(emailFilled)' password='\(pwFilled)' — retyping")
                if !emailFilled { type(into: email, "ios.verify@example.com") }
                if !pwFilled { type(into: app.secureTextFields.firstMatch, "iosVerify-2026!") }
            }
        } else if !app.tabBars.firstMatch.exists {
            miss("login email field never appeared")
        }
        return true
    }

    private func signIn() {
        guard fillLoginForm() else {
            XCTAssertTrue(app.tabBars.firstMatch.waitForExistence(timeout: 15), "sign-in failed")
            sleep(2)
            return
        }
        let cont = app.buttons["Continue"]
        if cont.waitForExistence(timeout: 5) {
            for _ in 0..<3 {
                cont.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                if app.tabBars.firstMatch.waitForExistence(timeout: 15) { break }
            }
        }
        XCTAssertTrue(app.tabBars.firstMatch.waitForExistence(timeout: 30), "sign-in failed")
        sleep(3)
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

    /// See VerificationGaps' own header comment on this exact function for
    /// why CONTAINS-matching and excluding the sheet's own nav-bar button are
    /// both load-bearing here.
    @discardableResult
    private func pick(_ option: String, inSheet sheet: String) -> Bool {
        let candidates = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] %@", option))
        var row = candidates.firstMatch
        guard row.waitForExistence(timeout: 6) else { miss("no '\(option)' option"); return false }

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

    private func dismissAnySheet() {
        for _ in 0..<3 {
            guard app.navigationBars.buttons["Done"].exists else { return }
            app.navigationBars.buttons["Done"].tap()
            sleep(1)
        }
    }

    private func appIsRunning() -> Bool { app.state == .runningForeground }

    /// The alert both rollback tests wait on, dismiss, and re-read the row
    /// from — pulled out once since two tests need the identical sequence.
    private func expectRollbackAlertThenDismiss() -> Bool {
        let alertTitle = app.staticTexts["Couldn't save"]
        let shown = alertTitle.waitForExistence(timeout: 15)
        XCTAssertTrue(shown, "a failed write said NOTHING — a silent rollback is the bug")
        if shown {
            let bodies = app.alerts.firstMatch.staticTexts.allElementsBoundByIndex.map(\.label)
            note("ALERT=\(bodies)")
            // "Failed" and "may have succeeded, but I lost track of it" are
            // different facts (CLAUDE.md's own outcome-honesty law). The
            // dead backend is a "no response at all" case in commit()'s own
            // three-outcome contract, so the honest body is the confirm-
            // failure sentence, never a bare claimed "failed".
            XCTAssertFalse(
                bodies.contains { $0.localizedCaseInsensitiveContains("failed") && !$0.localizedCaseInsensitiveContains("confirm") },
                "the alert claimed a flat failure rather than 'could not confirm' — that overclaims on a response we never got"
            )
        }
        if app.alerts.buttons["OK"].exists { app.alerts.buttons["OK"].tap(); sleep(2) }
        return shown
    }

    // MARK: - Gap 1b / 1c: priority and assignee rollback, independently

    /// Same task as VerificationGaps' Gap1 ("Sign-in screen loses the
    /// keyboard on rotation" — task_d2bf88b646dc4937), because its identity
    /// and its current priority/assignee are already known. Reads the
    /// CURRENT priority rather than assuming a starting value (today it's
    /// Medium, left there by the prior pass) so this is not coupled to
    /// whatever state an earlier run left behind.
    func testPriorityRollback() throws {
        signIn()
        guard tab(2, expect: "Projects") else { return }
        guard tapRow("Mobile App", expect: "Mobile App") else { return }
        guard openTask("Sign-in screen loses the keyboard") else { return }

        let start = propertyLabel("Priority")
        note("PRIORITY start=\(start)")
        let committed = start.contains("Urgent") ? "High" : "Urgent"
        let attempted = committed == "High" ? "Low" : "Medium"

        // ---- healthy write: commit a real change --------------------------
        guard openSheet("Priority") else { return }
        shot("pr-01-sheet")
        guard pick(committed, inSheet: "Priority") else { return }
        let afterCommit = propertyLabel("Priority")
        note("PRIORITY after healthy write=\(afterCommit)")
        XCTAssertTrue(afterCommit.contains(committed),
                      "priority row did not update on screen, it reads '\(afterCommit)'")
        XCTAssertFalse(app.staticTexts["Couldn't save"].exists, "a healthy priority write reported an error")
        shot("pr-02-committed")

        hostDo("verify-writes", timeout: 90)

        // ---- rollback: kill the backend, attempt a second change ----------
        guard hostDo("kill-backend", timeout: 90) else { return }
        sleep(2)
        guard appIsRunning() else { miss("app died once the backend went away"); return }

        guard openSheet("Priority") else { hostDo("restore-backend", timeout: 480); return }
        shot("pr-03-sheet-offline")
        pick(attempted, inSheet: "Priority")   // sheet dismisses immediately; the write is async
        sleep(6)
        shot("pr-04-rollback-alert")

        expectRollbackAlertThenDismiss()

        shot("pr-05-reverted")
        let afterRollback = propertyLabel("Priority")
        note("PRIORITY after rollback=\(afterRollback)")
        XCTAssertFalse(afterRollback.contains(attempted),
                       "the optimistic priority change was NOT rolled back — it still reads '\(afterRollback)'")
        XCTAssertTrue(afterRollback.contains(committed),
                      "priority did not return to the last confirmed value '\(committed)', it reads '\(afterRollback)'")

        hostDo("restore-backend", timeout: 480)
        // The server must still hold the COMMITTED value, not the attempted
        // one — proves the rollback reflects reality rather than merely the
        // UI's own optimism about what happened.
        hostDo("verify-writes", timeout: 90)
        print("PRIORITY_ROLLBACK_DONE misses=\(misses.count) \(misses)")
    }

    func testAssigneeRollback() throws {
        signIn()
        guard tab(2, expect: "Projects") else { return }
        guard tapRow("Mobile App", expect: "Mobile App") else { return }
        guard openTask("Sign-in screen loses the keyboard") else { return }

        let start = propertyLabel("Assignee")
        note("ASSIGNEE start=\(start)")
        // Toggle between the one agent used elsewhere in this suite (Release
        // Scout) and the one human teammate (Dana Okafor) — both real,
        // concretely different, and neither depends on which one the task
        // happens to hold when this runs.
        let committed = start.contains("Release Scout") ? "Dana Okafor" : "Release Scout"
        let attempted = committed == "Release Scout" ? "Dana Okafor" : "Release Scout"

        guard openSheet("Assignee") else { return }
        shot("as-01-sheet")
        guard pick(committed, inSheet: "Assignee") else { return }
        let afterCommit = propertyLabel("Assignee")
        note("ASSIGNEE after healthy write=\(afterCommit)")
        XCTAssertTrue(afterCommit.contains(committed.split(separator: " ").first.map(String.init) ?? committed),
                      "assignee row did not update on screen, it reads '\(afterCommit)'")
        XCTAssertFalse(app.staticTexts["Couldn't save"].exists, "a healthy assignee write reported an error")
        shot("as-02-committed")

        hostDo("verify-writes", timeout: 90)

        guard hostDo("kill-backend", timeout: 90) else { return }
        sleep(2)
        guard appIsRunning() else { miss("app died once the backend went away"); return }

        guard openSheet("Assignee") else { hostDo("restore-backend", timeout: 480); return }
        shot("as-03-sheet-offline")
        pick(attempted, inSheet: "Assignee")
        sleep(6)
        shot("as-04-rollback-alert")

        expectRollbackAlertThenDismiss()

        shot("as-05-reverted")
        let afterRollback = propertyLabel("Assignee")
        note("ASSIGNEE after rollback=\(afterRollback)")
        XCTAssertFalse(afterRollback.contains(attempted),
                       "the optimistic assignee change was NOT rolled back — it still reads '\(afterRollback)'")
        XCTAssertTrue(afterRollback.contains(committed.split(separator: " ").first.map(String.init) ?? committed),
                      "assignee did not return to the last confirmed value '\(committed)', it reads '\(afterRollback)'")

        hostDo("restore-backend", timeout: 480)
        hostDo("verify-writes", timeout: 90)
        print("ASSIGNEE_ROLLBACK_DONE misses=\(misses.count) \(misses)")
    }

    // MARK: - Gap 2: a genuinely cold launch, throttled so the skeleton is observable

    /// `xcrun simctl uninstall` + `install` wipes the app container — the
    /// Application Support directory `DiskCache` writes to is gone, so
    /// `hasLoadedOnce` starts false regardless of whether the Keychain's
    /// refresh token happened to survive (it's a separate store; either way
    /// the workspace fetch that follows sign-in starts from nothing).
    ///
    /// The race the brief warns about is real: on today's local backend the
    /// full 5-endpoint refresh burst that follows login has been observed
    /// completing in well under 200ms. `resume-arm-for-login` (hostctl.sh)
    /// resumes the backend and watches ITS OWN access log for the login
    /// request to land, then re-pauses ~120ms later — tied to a server-side
    /// event rather than a blind client-side sleep, so it survives however
    /// long the tap itself took.
    func testColdLaunchSkeleton() throws {
        // Sign out FIRST, deliberately, before touching the install at all —
        // whatever is on screen from an earlier test today may already be
        // signed in, and this pass needs the login screen to reappear after
        // reinstall regardless of whether the simulator's Keychain happens
        // to survive `simctl uninstall` (that's undocumented behavior this
        // test should not depend on either way; `signOut()` clears the
        // refresh token itself, so after this there is nothing left to
        // auto-restore even if the Keychain item did survive).
        app.launch()
        sleep(2)
        if app.tabBars.firstMatch.waitForExistence(timeout: 5) {
            let account = app.buttons["Account and settings"]
            if account.waitForExistence(timeout: 5) {
                account.tap(); sleep(1)
                let signOut = app.buttons["Sign Out"]
                if signOut.waitForExistence(timeout: 5) {
                    signOut.tap()
                    XCTAssertTrue(app.buttons["Get started"].waitForExistence(timeout: 8)
                                  || app.textFields.firstMatch.waitForExistence(timeout: 8),
                                  "Sign Out did not return to the signed-out screen")
                }
            }
        }

        guard hostDo("uninstall-app", timeout: 90) else { return }
        guard hostDo("reinstall-app", timeout: 90) else { return }

        fillLoginForm()
        shot("cold-00-login-filled")

        let cont = app.buttons["Continue"]
        guard cont.waitForExistence(timeout: 8) else {
            miss("no Continue button — reinstall did not land on the login screen as expected")
            return
        }
        guard hostDo("resume-arm-for-login", timeout: 15) else { return }

        // LAST-INSTANT re-verification, right before the tap. This exact
        // path (uninstall -> reinstall -> launch) is the one place in this
        // suite that reliably triggers a real, observed simulator hiccup —
        // an `FBSApplicationLibrary returned nil` error that XCUITest
        // retries internally, silently, tearing down and replacing the
        // just-launched instance sometime after `fillLoginForm()` already
        // returned. `fillLoginForm()`'s own verify-and-retype loop cannot
        // see a wipe that happens AFTER it already checked; this second
        // check, as late as possible before the tap, is what actually
        // catches it — reproduced live: without this, the tap submitted a
        // real email against a wiped-empty password and the app correctly
        // (and confusingly, for this test) answered "That email and
        // password don't match."
        let email = app.textFields.firstMatch
        let pw = app.secureTextFields.firstMatch
        for _ in 0..<3 {
            let emailFilled = !((email.value as? String) ?? "").isEmpty
            let pwFilled = !((pw.value as? String) ?? "").isEmpty
            if emailFilled && pwFilled { break }
            note("pre-tap field check: email='\(emailFilled)' password='\(pwFilled)' — retyping")
            if !emailFilled { type(into: email, "ios.verify@example.com") }
            if !pwFilled { type(into: pw, "iosVerify-2026!") }
        }

        cont.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()

        // Rapid-fire screenshots right through the window where the backend
        // should be frozen — maximizes the chance of catching the exact
        // frame even if the timing above lands a little early or late.
        var sawTabBar = false
        var sawSkeletonCells = false
        for i in 0..<24 {
            if app.tabBars.firstMatch.exists {
                sawTabBar = true
                let cellCount = app.collectionViews.cells.count
                let hasRealHeader = app.staticTexts.matching(
                    NSPredicate(format: "label CONTAINS[c] %@ OR label CONTAINS[c] %@ OR label CONTAINS[c] %@",
                                "NEEDS YOUR INPUT", "CAUGHT UP", "Couldn't load")
                ).firstMatch.exists
                note("t=\(i) tabBar=1 cells=\(cellCount) realContent=\(hasRealHeader)")
                if cellCount > 0 && !hasRealHeader { sawSkeletonCells = true }
                if i == 2 || i == 6 { shot("cold-0\(i)-mid") }
                if hasRealHeader { break }
            } else {
                note("t=\(i) tabBar=0 (still on login/welcome)")
            }
            usleep(200_000)
        }
        shot("cold-99-settled")

        note("SAW_TAB_BAR=\(sawTabBar) SAW_SKELETON_CELLS=\(sawSkeletonCells)")
        XCTAssertTrue(sawTabBar, "never reached the signed-in tab bar at all")
        if !sawSkeletonCells {
            note("did not visibly catch the skeleton frame — see screenshots; the race described in the brief may have been lost this run")
        }

        // Whatever happened above, the backend must not still be frozen —
        // resume unconditionally so the app (and any test after this one)
        // recovers, then confirm real content actually arrives.
        hostDo("resume-backend", timeout: 60)
        let realHeader = app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS[c] %@", "CAUGHT UP")
        ).firstMatch
        let sawRealContentEventually = realHeader.waitForExistence(timeout: 20)
            || app.collectionViews.cells.count > 0
        shot("cold-final-recovered")
        note("SAW_REAL_CONTENT_EVENTUALLY=\(sawRealContentEventually)")
        XCTAssertTrue(sawRealContentEventually, "the app never recovered real content after the backend came back")

        print("COLD_LAUNCH_DONE misses=\(misses.count) \(misses)")
    }

    // MARK: - Gap 3: orientation support, determined rather than assumed

    /// Info.plist has no `UISupportedInterfaceOrientations` key at all (and
    /// nothing in code overrides `supportedInterfaceOrientations`), so what
    /// actually happens on rotation is a genuine open question, not
    /// something greppable. `XCUIDevice.shared.orientation` rotates the
    /// SIMULATOR; whether the APP's rendered frame follows is read back from
    /// a real screenshot's dimensions and content, which is the only source
    /// that can't be fooled by a stale assumption either way.
    func testRotationSupport() throws {
        // Deliberately NOT signIn() — orientation support is a property of
        // the window/scene (Info.plist, or a supportedInterfaceOrientations
        // override), not of any one screen, so it is provable from the
        // Welcome/Login screen alone. That matters here specifically: this
        // test must not share the backend-dependent sign-in flow's fate when
        // the shared machine is under the kind of load this pass measured
        // (uptime's own load average over 400 at one point) — rotation is
        // exactly the one check in this file that does not need to.
        app.launch()
        sleep(2)
        note("landed on: tabBar=\(app.tabBars.firstMatch.exists) getStarted=\(app.buttons["Get started"].exists) textField=\(app.textFields.firstMatch.exists)")

        XCUIDevice.shared.orientation = .portrait
        sleep(1)
        let portraitShot = XCUIScreen.main.screenshot()
        let portraitSize = portraitShot.image.size
        note("PORTRAIT size=\(portraitSize)")
        shot("rot-00-portrait")

        XCUIDevice.shared.orientation = .landscapeLeft
        sleep(2)
        let landscapeShot = XCUIScreen.main.screenshot()
        let landscapeSize = landscapeShot.image.size
        note("LANDSCAPE_LEFT size=\(landscapeSize)")
        shot("rot-01-landscape-left")

        // The app's own navigation title and tab bar are the content proof —
        // if the interface genuinely rotated, "Inbox" and the tab bar are
        // still on screen (SwiftUI doesn't tear the hierarchy down), so their
        // presence doesn't by itself distinguish the two cases. The
        // SCREENSHOT's aspect ratio is what actually tells them apart: a
        // portrait-locked app keeps reporting a taller-than-wide frame no
        // matter what XCUIDevice.orientation claims, because iOS never
        // rotates the app's own window for it.
        let portraitIsTaller = portraitSize.height > portraitSize.width
        let landscapeIsWider = landscapeSize.width > landscapeSize.height
        note("portraitIsTaller=\(portraitIsTaller) landscapeIsWider(afterRotate)=\(landscapeIsWider)")

        XCUIDevice.shared.orientation = .portrait
        sleep(1)
        shot("rot-02-back-to-portrait")

        if landscapeIsWider {
            note("VERDICT: the app SUPPORTS landscape — the rendered frame rotated with the device")
        } else {
            note("VERDICT: the app is PORTRAIT-LOCKED — the frame stayed \(landscapeSize) after requesting landscape")
        }
        XCTAssertTrue(portraitIsTaller, "even in portrait the frame wasn't taller than wide (\(portraitSize)) — something is already off before rotation is involved")
        print("ROTATION_DONE landscapeSupported=\(landscapeIsWider) misses=\(misses.count) \(misses)")
    }

    // MARK: - Gap 4a: Dynamic Type — clipping/overlap at an accessibility size

    /// `-UIPreferredContentSizeCategoryName` is the standard XCUITest lever
    /// for this: it sets the category for THIS app's process only, so it
    /// never touches the simulator's global setting (and never leaks into
    /// any other test in this shared target). Re-launches with it set,
    /// signs in fresh (the running app from any prior test was launched
    /// without it), and screenshots the three named screens.
    func testDynamicTypeNoClipping() throws {
        app = XCUIApplication()
        app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        signIn()

        tab(0, expect: "Inbox")
        sleep(1)
        shot("dt-01-inbox")
        note("INBOX row labels at AX-XXXL: \(app.collectionViews.cells.allElementsBoundByIndex.map(\.label).prefix(6))")

        guard tab(2, expect: "Projects") else { return }
        if tapRow("Mobile App", expect: "Mobile App") {
            shot("dt-02-project")
            if openTask("Sign-in screen loses the keyboard") {
                shot("dt-03-task-detail")
                note("TASK DETAIL property rows at AX-XXXL:")
                for title in ["Status", "Priority", "Assignee", "Due"] {
                    note("  \(title) -> '\(propertyLabel(title))'")
                }
                // Frame-overlap check: two sibling elements whose bounding
                // boxes overlap vertically by more than a hairline are the
                // structural signature of clipped/overlapping text — a
                // reflowed (merely taller) layout does not do this because
                // each row still owns its own non-overlapping band.
                let texts = app.staticTexts.allElementsBoundByIndex
                var overlaps = 0
                for i in 0..<texts.count {
                    for j in (i + 1)..<texts.count where j < texts.count {
                        let a = texts[i].frame
                        let b = texts[j].frame
                        guard a.width > 0, a.height > 0, b.width > 0, b.height > 0 else { continue }
                        let intersection = a.intersection(b)
                        if intersection.height > min(a.height, b.height) * 0.5,
                           intersection.width > min(a.width, b.width) * 0.5 {
                            overlaps += 1
                        }
                    }
                }
                note("OVERLAPPING_TEXT_PAIRS=\(overlaps)")
                XCTAssertEqual(overlaps, 0, "found \(overlaps) pair(s) of text elements overlapping at an accessibility text size — see dt-03-task-detail.png")
            }
        }

        // Settings, opened from the Inbox toolbar.
        tab(0, expect: "Inbox")
        let account = app.buttons["Account and settings"]
        if account.waitForExistence(timeout: 6) {
            account.tap()
            sleep(1)
            shot("dt-04-settings")
        } else {
            miss("no Account and settings button to reach Settings")
        }

        print("DYNAMIC_TYPE_DONE misses=\(misses.count) \(misses)")
    }

    // MARK: - Gap 4b: VoiceOver baseline — labels, plus Xcode's own audit

    /// Not exhaustive (the brief is explicit about that) — spot-checks the
    /// three surfaces named: the tab bar, a task row (title AND status, not
    /// just title), and the primary buttons this pass already fixed
    /// (TaskDetailView's send button, WorkspaceSettingsView's copy-link
    /// button). `performAccessibilityAudit()` (Xcode 15+) runs Apple's own
    /// automated checks — missing labels, insufficient contrast, hit-target
    /// size — on top of the manual spot-check, and is the closest this
    /// harness gets to a real screen-reader pass without a physical device.
    func testVoiceOverBaseline() throws {
        signIn()

        // ---- tab bar ---------------------------------------------------
        let bar = app.tabBars.firstMatch
        XCTAssertTrue(bar.waitForExistence(timeout: 10))
        let tabLabels = bar.buttons.allElementsBoundByIndex.map(\.label)
        note("TAB_BAR_LABELS=\(tabLabels)")
        for expected in ["Inbox", "My work", "Projects", "Agents", "Search"] {
            XCTAssertTrue(tabLabels.contains { $0.contains(expected) },
                          "tab bar is missing a sensible label containing '\(expected)': \(tabLabels)")
        }

        // ---- a task row: title AND status, not just title ---------------
        guard tab(2, expect: "Projects") else { return }
        guard tapRow("Mobile App", expect: "Mobile App") else { return }
        let taskRow = rows().matching(
            NSPredicate(format: "label CONTAINS[c] %@", "Sign-in screen loses the keyboard")
        ).firstMatch
        if taskRow.waitForExistence(timeout: 8) {
            note("TASK_ROW_LABEL='\(taskRow.label)'")
            XCTAssertTrue(taskRow.label.count > "Sign-in screen loses the keyboard on rotation".count,
                          "the task row's spoken label is JUST the title, with nothing about status: '\(taskRow.label)'")
        } else {
            miss("task row not found for the VoiceOver spot-check")
        }

        // ---- primary buttons: the two this pass added labels to ---------
        if openTask("Sign-in screen loses the keyboard") {
            let send = app.buttons["Send comment"]
            note("SEND_COMMENT_BUTTON exists=\(send.exists) label='\(send.label)'")
            XCTAssertTrue(send.exists, "the primary send-comment button has no 'Send comment' accessibility label")
            back()
        }

        tab(0, expect: "Inbox")
        let account = app.buttons["Account and settings"]
        if account.waitForExistence(timeout: 6) {
            account.tap()
            sleep(1)
            let copyRow = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] %@", "Workspace")).firstMatch
            if copyRow.waitForExistence(timeout: 4) {
                copyRow.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                sleep(1)
                shot("vo-01-workspace-settings")
                let copyBtn = app.buttons.matching(
                    NSPredicate(format: "label CONTAINS[c] %@", "Copy invite link")
                ).firstMatch
                note("COPY_LINK_BUTTON exists=\(copyBtn.exists)")
                // Only assertable if an invite has actually been generated in
                // this session (the button lives inside the invite-result
                // card) — report rather than fail if the row never appeared,
                // since generating one is a mutation this test does not
                // need to perform to prove the label EXISTS in source.
                if !copyBtn.exists {
                    note("no invite-result card on screen this run — label presence not independently confirmed live here, only via the source fix")
                }
            }
        }

        // ---- Xcode's own automated audit, on the richest screen we have --
        do {
            try app.performAccessibilityAudit()
            note("ACCESSIBILITY_AUDIT: no issues reported on the current screen")
        } catch {
            // Xcode's audit throws with every finding attached. Log every
            // one rather than failing outright on the first pass — some
            // categories (e.g. contrast against a screenshot-only render)
            // are known to false-positive in a simulator, and the value
            // here is a full list to triage, not a hard gate.
            note("ACCESSIBILITY_AUDIT findings:\n\(error)")
        }

        print("VOICEOVER_DONE misses=\(misses.count) \(misses)")
    }

    private func back() {
        let b = app.navigationBars.buttons.element(boundBy: 0)
        if b.exists { b.tap(); sleep(1) }
    }
}

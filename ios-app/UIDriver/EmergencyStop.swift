import XCTest

/// Drives the workspace-wide Emergency Stop control end to end: Settings ▸
/// Workspace ▸ "Stop all agents" (with a reason) ▸ confirm ▸ "All agents
/// stopped" renders ▸ "Resume all agents" ▸ the trigger reappears. Screenshots
/// land in `/tmp/emp-shots/$SHOT_LABEL/`, same convention as the rest of
/// `UIDriver/`.
///
/// This test proves the UI reaches the right STATE. It deliberately does NOT
/// itself prove the server agrees — that is done separately, outside the
/// simulator, by re-`curl`ing `GET /w/{id}/fleet/workspace` before and after
/// each phase against the same seeded backend this app talks to. A UI test
/// asserting its own optimistic render would be checking the button against
/// itself; the real proof is a second, independent reader of the same state.
final class EmergencyStop: XCTestCase {

    private var app: XCUIApplication!
    private var shotDir: URL!
    private var step = 0
    private var misses: [String] = []

    override func setUpWithError() throws {
        continueAfterFailure = true
        let label = ProcessInfo.processInfo.environment["SHOT_LABEL"] ?? "emergency-stop"
        let base = URL(fileURLWithPath: "/tmp/emp-shots").appendingPathComponent(label)
        try? FileManager.default.removeItem(at: base)
        try? FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        shotDir = base
        app = XCUIApplication()
    }

    // MARK: - plumbing (same idioms as SettingsCheck.swift / VerificationGaps.swift)

    private func shot(_ name: String) {
        step += 1
        let png = XCUIScreen.main.screenshot().pngRepresentation
        let file = shotDir.appendingPathComponent(String(format: "%02d-%@.png", step, name))
        try? png.write(to: file)
        print("SHOT \(file.path)")
    }

    private func miss(_ s: String) { misses.append(s); print("ESTOP_MISS \(s)") }
    private func note(_ s: String) { print("ESTOP_NOTE \(s)") }

    private func hasFocus(_ e: XCUIElement) -> Bool {
        (e.value(forKey: "hasKeyboardFocus") as? Bool) ?? false
    }

    /// Types in small CHUNKS with a pause between them, never the whole
    /// string in one `typeText` call. A single-call `typeText` on a field
    /// with autocorrection ENABLED (this app's reason field, deliberately —
    /// unlike the email field elsewhere, which disables it) reproducibly
    /// truncated to 2 characters here: iOS's predictive-text bar intercepts
    /// synthesized keystrokes arriving faster than it can settle, and
    /// XCUITest has no way to wait for that settle mid-call. Confirmed via a
    /// screenshot taken immediately after a "successful" typeText call
    /// (before any other code ran) showing only "iO" of a much longer
    /// string. Chunking gives the predictive bar time to resolve between
    /// bursts, which is the standard workaround for this exact XCUITest
    /// limitation — not a defect in the app being typed into.
    private func type(into field: XCUIElement, _ text: String) {
        for _ in 0..<4 {
            if !hasFocus(field) {
                field.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                usleep(700_000)
            }
            if hasFocus(field) {
                for chunk in text.chunked(into: 4) {
                    field.typeText(chunk)
                    usleep(250_000)
                }
                return
            }
            sleep(1)
        }
        miss("could not focus the reason field")
    }

    private func signIn() {
        app.launch(); sleep(2)
        // "Get started" now opens the system Safari sheet, which this harness
        // cannot drive — see SignInFlow.swift. The in-app door is the
        // secondary "Sign in with email instead" link.
        if !SignInFlow.reachEmailForm(app) {
            miss("could not reach the login form from the welcome screen")
        }
        let email = app.textFields.firstMatch
        if email.waitForExistence(timeout: 6) {
            type(into: email, "ios.verify@example.com")
            let pw = app.secureTextFields.firstMatch
            if pw.waitForExistence(timeout: 4) { type(into: pw, "iosVerify-2026!") }
            if app.buttons["Continue"].exists {
                for _ in 0..<3 {
                    app.buttons["Continue"].coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                    if app.tabBars.firstMatch.waitForExistence(timeout: 15) { break }
                }
            }
        }
        XCTAssertTrue(app.tabBars.firstMatch.waitForExistence(timeout: 30), "sign-in failed")
        sleep(5)
    }

    /// A row inside the Settings List — same query SettingsCheck.swift's own
    /// `rows()` uses (SwiftUI's `List` backs onto a `UICollectionView`).
    private func rows() -> XCUIElementQuery { app.collectionViews.buttons }

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

    /// Scrolls the Workspace List down until a button whose label CONTAINS
    /// `text` is hittable, then taps it. Emergency Stop is the LAST section
    /// on this screen (below Members / Invite / Pending invites), so it is
    /// off-screen on a normal-height device until scrolled into view.
    ///
    /// `app.swipeUp()` (whole-app, screen-centre anchor) is used deliberately
    /// rather than a collection-view-scoped swipe — that was tried and
    /// caused a WORSE, harder-to-diagnose failure (the Emergency Stop section
    /// stopped being found after several rapid swipes at all, screenshot
    /// evidence showed the list ending after "Invite someone" with nothing
    /// below it, most likely a SwiftUI List virtualization artifact from
    /// synthetic-gesture scroll speed outpacing lazy layout). `app.swipeUp()`
    /// is the same mechanism `SettingsCheck.swift`/`VerificationGaps.swift`
    /// already use successfully elsewhere in this harness. The real hazard
    /// this once caused — a drag starting inside the reason TextField right
    /// after typing acting as a text-selection gesture and silently clearing
    /// the SwiftUI binding while the ON-SCREEN text still showed the typed
    /// value — is addressed at the call site instead, by resigning first
    /// responder on a neutral element before any scroll happens.
    @discardableResult
    private func scrollToAndTap(_ text: String, excluding excludedLabel: String? = nil) -> Bool {
        var candidates = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] %@", text))
        var e = candidates.firstMatch
        for _ in 0..<10 {
            if e.exists && e.isHittable { break }
            app.swipeUp(); usleep(400_000)
            candidates = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] %@", text))
            e = candidates.firstMatch
        }
        guard e.exists else { miss("no button containing '\(text)' found after scrolling"); return false }
        if let excludedLabel, e.label == excludedLabel {
            // Pick the next match that isn't the excluded one (mirrors
            // VerificationGaps.swift's `pick(_:inSheet:)` nav-bar exclusion).
            let all = candidates.allElementsBoundByIndex.filter { $0.label != excludedLabel }
            guard let other = all.first else { miss("'\(text)' only matched the excluded label"); return false }
            e = other
        }
        guard e.isHittable else { miss("button containing '\(text)' exists but is not hittable"); return false }
        e.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        return true
    }

    private func back() {
        let b = app.navigationBars.buttons.element(boundBy: 0)
        if b.exists { b.tap(); sleep(2) }
    }

    private func stoppedCardVisible() -> Bool {
        app.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] %@", "All agents stopped")).firstMatch.exists
    }

    private func stopTriggerVisible() -> Bool {
        app.buttons.matching(NSPredicate(format: "label CONTAINS[c] %@", "Stop all agents")).firstMatch.exists
    }

    // MARK: - shared navigation

    /// Sign in, open Settings, open Workspace. Shared by both phases below so
    /// each is a genuinely independent app launch — proving the SERVER state
    /// persisted rather than merely surviving in one long-lived process.
    private func navigateToWorkspaceSettings() {
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

        guard tapRowByLabel("Workspace", waitFor: { self.app.navigationBars["Workspace"].exists }) else { return }
        sleep(2)
    }

    // MARK: - Phase 1: stop only — leaves the workspace stopped on exit

    /// Taps "Stop all agents", types a reason, confirms, and asserts the
    /// "All agents stopped" card renders — then STOPS, deliberately not
    /// resuming. The caller (outside this process, via a separate `curl`)
    /// re-reads `GET /w/{id}/fleet/workspace` while the app is in this state,
    /// which is the actual proof the stop reached the server rather than
    /// only this button's own optimistic render. See `testResumeOnly` for
    /// the other half.
    func testStopOnly() throws {
        navigateToWorkspaceSettings()
        shot("00-workspace-baseline")

        // Baseline: server was confirmed {"active": false} moments before this
        // run via a direct curl (outside the simulator) — this is the plain
        // trigger, not the stopped card.
        guard scrollToAndTap("Stop all agents") else {
            shot("00b-no-trigger-found")
            return
        }
        sleep(1)
        shot("01-confirm-form")

        guard app.buttons.matching(NSPredicate(format: "label CONTAINS[c] %@", "Confirm stop")).firstMatch
            .waitForExistence(timeout: 6) else {
            miss("confirm form did not render after tapping the trigger"); return
        }

        let reasonField = app.textFields.firstMatch
        if reasonField.waitForExistence(timeout: 4) {
            type(into: reasonField, "iOS UIDriver verification EmergencyStop.swift")
        } else {
            miss("no reason text field found on the confirm form")
        }
        shot("02-reason-typed")

        // Resign first responder on a NEUTRAL, non-interactive element (the
        // form's own static heading) before any scroll — see
        // scrollToAndTap's own comment for why a gesture starting inside a
        // live text field is unsafe here.
        let heading = app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS[c] %@", "Stop every agent")
        ).firstMatch
        if heading.exists { heading.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap() }
        usleep(400_000)

        guard scrollToAndTap("Confirm stop") else { return }

        let stoppedDeadline = Date().addingTimeInterval(15)
        var sawStopped = false
        while Date() < stoppedDeadline {
            if stoppedCardVisible() { sawStopped = true; break }
            usleep(300_000)
        }
        sleep(1)
        shot("03-stopped-card")
        XCTAssertTrue(sawStopped, "the 'All agents stopped' card never rendered after confirming")
        if !sawStopped { miss("stopped card never rendered") }

        // No dead controls: the trigger must be GONE while stopped.
        XCTAssertFalse(stopTriggerVisible(), "the 'Stop all agents' trigger is still rendered while active — a dead/duplicate control")

        print("ESTOP_STOPPED \(shotDir.path) misses=\(misses.count) \(misses)")
    }

    // MARK: - Phase 2: resume only — starts from a FRESH launch

    /// A brand-new app launch against a workspace the previous phase left
    /// stopped server-side. Asserts the stopped card renders on arrival
    /// (proof the server, not just in-memory app state, is what is being
    /// read), taps "Resume all agents", and asserts the plain trigger
    /// reappears. The caller re-`curl`s afterward to confirm the server
    /// agrees.
    func testResumeOnly() throws {
        navigateToWorkspaceSettings()

        let arrivedDeadline = Date().addingTimeInterval(15)
        var sawStoppedOnArrival = false
        while Date() < arrivedDeadline {
            if stoppedCardVisible() { sawStoppedOnArrival = true; break }
            usleep(300_000)
        }
        shot("00-arrives-already-stopped")
        XCTAssertTrue(sawStoppedOnArrival, "a fresh launch did not show the stopped card — the prior phase's stop did not persist server-side")
        if !sawStoppedOnArrival { miss("fresh launch did not read the stopped state from the server"); return }

        guard scrollToAndTap("Resume all agents") else { return }

        let resumedDeadline = Date().addingTimeInterval(15)
        var sawTrigger = false
        while Date() < resumedDeadline {
            if stopTriggerVisible() && !stoppedCardVisible() { sawTrigger = true; break }
            usleep(300_000)
        }
        sleep(1)
        shot("01-resumed")
        XCTAssertTrue(sawTrigger, "the plain 'Stop all agents' trigger never reappeared after resuming")
        if !sawTrigger { miss("trigger never reappeared after resume") }
        XCTAssertFalse(stoppedCardVisible(), "the stopped card is still rendered after resuming")

        print("ESTOP_RESUMED \(shotDir.path) misses=\(misses.count) \(misses)")
    }
}

private extension String {
    /// Splits into fixed-size pieces, last one short — used by `type(into:_:)`
    /// to pace synthesized keystrokes past iOS's predictive-text bar.
    func chunked(into size: Int) -> [String] {
        guard size > 0, !isEmpty else { return [self] }
        var pieces: [String] = []
        var index = startIndex
        while index < endIndex {
            let end = self.index(index, offsetBy: size, limitedBy: endIndex) ?? endIndex
            pieces.append(String(self[index..<end]))
            index = end
        }
        return pieces
    }
}

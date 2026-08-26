import XCTest

/// Live, on-device proof for the document-editing feature (title/body edit,
/// explicit Save, the stale-write 409 conflict banner) — the ONE thing a
/// unit test cannot show: what actually renders on screen when the
/// precondition genuinely refuses a write.
///
/// THE CONFLICT IS TRIGGERED BY ORDERING, NOT BY A LIVE MID-TYPING PAUSE.
/// `DocumentEditSheet`'s `baseSha256` is fixed at whatever
/// `DocumentDetailView.loaded.stateSha256` was the moment the sheet opened.
/// So: open the document (captures sha S1) -> ask the HOST to edit it
/// server-side (advances the real sha to S2, exactly as an agent editing the
/// same document would) -> THEN tap the pencil. The sheet opens holding the
/// now-stale S1, edits it, and Save must 409 -- the identical shape a real
/// "an agent changed this while you were looking at it" collision produces,
/// without needing to pause XCUITest mid-keystroke.
///
/// Same `hostDo` two-file handshake `VerificationGaps.swift` established
/// (the simulator shares the host's `/tmp`), in its own control directory
/// (`/tmp/emp-ctl-docs`) and its own request name so this cannot collide
/// with that file's or any other concurrent harness's requests. The host
/// responder is `conflict_responder.sh` (run separately, alongside this
/// test) — it re-reads the document's CURRENT sha before patching, so it
/// works no matter what state a previous run left the document in.
final class DocumentEditVerify: XCTestCase {

    private var app: XCUIApplication!
    private var shotDir: URL!
    private var step = 0
    private var misses: [String] = []

    private static let ctlDir = URL(fileURLWithPath: "/tmp/emp-ctl-docs")

    override func setUpWithError() throws {
        continueAfterFailure = true
        let label = ProcessInfo.processInfo.environment["SHOT_LABEL"] ?? "doc-edit"
        let base = URL(fileURLWithPath: "/tmp/emp-shots").appendingPathComponent(label)
        try? FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        shotDir = base
        try? FileManager.default.createDirectory(at: Self.ctlDir, withIntermediateDirectories: true)
        app = XCUIApplication()
    }

    // MARK: - plumbing (ported from VerificationGaps.swift's own pattern)

    private func shot(_ name: String) {
        step += 1
        let png = XCUIScreen.main.screenshot().pngRepresentation
        let file = shotDir.appendingPathComponent(String(format: "%02d-%@.png", step, name))
        try? png.write(to: file)
        print("SHOT \(file.path)")
    }

    private func miss(_ s: String) { misses.append(s); print("DOC_MISS \(s)") }
    private func note(_ s: String) { print("DOC_NOTE \(s)") }

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

    private func signIn() {
        app.launch()
        sleep(2)
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
        sleep(4)
    }

    @discardableResult
    private func tab(_ index: Int, expect navBar: String) -> Bool {
        let bar = app.tabBars.firstMatch
        guard bar.waitForExistence(timeout: 10), bar.buttons.count > index else {
            miss("tab bar missing for index \(index)"); return false
        }
        for _ in 0..<3 {
            reliableTap(bar.buttons.element(boundBy: index))
            if app.navigationBars[navBar].waitForExistence(timeout: 8) { sleep(2); return true }
            sleep(2)
        }
        miss("tab \(index) did not reach '\(navBar)'")
        dumpTree("tab-\(index)-failure")
        return false
    }

    /// Prints every nav bar / static text / button label currently on
    /// screen, and saves the raw accessibility tree to a scratch file — the
    /// fastest way to see what a failed navigation actually landed on
    /// without re-running the whole test for one more screenshot.
    private func dumpTree(_ label: String) {
        shot(label)
        let navBars = app.navigationBars.allElementsBoundByIndex.map(\.identifier)
        note("TREE[\(label)] navBars=\(navBars)")
        let texts = app.staticTexts.allElementsBoundByIndex.prefix(20).map(\.label)
        note("TREE[\(label)] staticTexts(first 20)=\(texts)")
        let buttons = app.buttons.allElementsBoundByIndex.prefix(20).map(\.label)
        note("TREE[\(label)] buttons(first 20)=\(buttons)")
        let full = app.debugDescription
        let file = shotDir.appendingPathComponent("\(label)-tree.txt")
        try? full.write(to: file, atomically: true, encoding: .utf8)
        note("TREE[\(label)] full dump at \(file.path)")
    }

    private func rows() -> XCUIElementQuery { app.collectionViews.buttons }

    @discardableResult
    private func tapRow(_ text: String) -> Bool {
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
            sleep(2)
            return true
        }
        miss("row '\(text)' never tapped")
        return false
    }

    /// A `PickerRow`/composite-label button never matches an exact label —
    /// `DocumentEditSheet`'s own conflict actions are two stacked `Text`s
    /// (title + consequence) inside one Button, so the real accessibility
    /// label is their concatenation. CONTAINS is the only reliable match,
    /// same lesson `VerificationGaps.pick(_:inSheet:)` already learned.
    private func button(containing text: String) -> XCUIElement {
        app.buttons.matching(NSPredicate(format: "label CONTAINS[c] %@", text)).firstMatch
    }

    private func tapButton(containing text: String, label: String) -> Bool {
        let b = button(containing: text)
        guard b.waitForExistence(timeout: 8) else { miss("no '\(label)' button"); return false }
        reliableTap(b)
        return true
    }

    /// A plain `.tap()` has repeatedly resolved, reported hittable, and
    /// silently done nothing in this harness (README's own documented
    /// gotcha) — every tap in this file goes through this instead.
    private func reliableTap(_ e: XCUIElement) {
        e.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
    }

    // MARK: - The proof

    func testDocumentEditAndConflict() throws {
        signIn()
        dumpTree("after-signin")

        // ── Reach the seeded verify document ────────────────────────────
        guard tab(2, expect: "Projects") else { return }
        guard tapRow("General") else { miss("no 'General' project row"); return }
        sleep(1)
        // ProjectDetailView's segmented Tasks/Documents picker.
        let documentsSegment = app.buttons["Documents"]
        if documentsSegment.waitForExistence(timeout: 6) {
            reliableTap(documentsSegment)
            sleep(1)
        } else {
            miss("no Documents segment on the project screen")
        }
        shot("00-documents-list")
        guard tapRow("iOS Edit Verify") else {
            miss("seeded verify document not found — was it created in workspace ws_6d2846c045de, project General?")
            return
        }
        sleep(2)
        shot("01-document-opened")

        // ── HAPPY PATH: edit, save, see it rendered ─────────────────────
        let editButton = app.buttons["Edit document"]
        guard editButton.waitForExistence(timeout: 10) else {
            miss("no 'Edit document' pencil — canWrite must have resolved false, or the document never finished loading")
            return
        }
        reliableTap(editButton)
        sleep(1)
        guard app.navigationBars["Edit document"].waitForExistence(timeout: 8) else {
            miss("the editor sheet never opened"); return
        }
        shot("02-editor-opened")

        let bodyEditor = app.textViews.firstMatch
        guard bodyEditor.waitForExistence(timeout: 6) else { miss("no body text editor"); return }
        let happyStamp = "Happy-path edit from the phone, stamp \(Int(Date().timeIntervalSince1970))."
        bodyEditor.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.9)).tap()
        usleep(500_000)
        bodyEditor.typeText("\n\n\(happyStamp)")
        shot("03-happy-edit-typed")

        let saveButton = app.navigationBars.buttons["Save"]
        guard saveButton.waitForExistence(timeout: 6) else { miss("no Save button"); return }
        reliableTap(saveButton)

        // The sheet dismisses on a confirmed save.
        let dismissed = waitUntilGone(app.navigationBars["Edit document"], timeout: 15)
        XCTAssertTrue(dismissed, "the editor did not dismiss after a healthy save")
        sleep(2)
        shot("04-happy-save-result")
        XCTAssertTrue(
            app.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] %@", happyStamp)).firstMatch.waitForExistence(timeout: 8),
            "the saved edit is not rendered back in the document view"
        )
        note("HAPPY PATH: edit saved and rendered correctly")

        // ── THE CONFLICT: host edits the document server-side while the
        // phone's DocumentDetailView still holds the sha it just saved,
        // THEN we open the editor (capturing that now-stale sha), edit,
        // and Save must be refused. ─────────────────────────────────────
        guard hostDo("conflict-edit", timeout: 60) else { return }
        sleep(1)

        reliableTap(editButton)
        sleep(1)
        guard app.navigationBars["Edit document"].waitForExistence(timeout: 8) else {
            miss("the editor did not reopen for the conflict scenario"); return
        }
        shot("05-conflict-editor-reopened")

        let bodyEditor2 = app.textViews.firstMatch
        guard bodyEditor2.waitForExistence(timeout: 6) else { miss("no body text editor on reopen"); return }
        bodyEditor2.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.9)).tap()
        usleep(500_000)
        bodyEditor2.typeText("\n\nMY edit, typed while a stale base_sha256 is held.")
        shot("06-conflict-edit-typed")

        let saveButton2 = app.navigationBars.buttons["Save"]
        guard saveButton2.waitForExistence(timeout: 6) else { miss("no Save button on reopen"); return }
        reliableTap(saveButton2)

        // ── What the person actually sees on a refusal ──────────────────
        let headline = app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS[c] %@", "changed this document while you were editing")
        ).firstMatch
        let sawConflict = headline.waitForExistence(timeout: 15)
        sleep(1)
        shot("07-conflict-banner")

        XCTAssertTrue(sawConflict, "the 409 conflict never rendered as the conflict banner — either the write silently succeeded (data loss) or it showed a generic error instead")

        // The reassurance and BOTH explicit actions must be visible — this
        // is the whole reason the feature exists: neither side's text may
        // be thrown away, and the choice must be explicit.
        XCTAssertTrue(
            app.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] %@", "have not been saved")).firstMatch.exists,
            "the reassurance that the draft is intact is not on screen"
        )
        XCTAssertTrue(button(containing: "Keep my version").exists, "no 'Keep my version' action")
        XCTAssertTrue(button(containing: "Use theirs instead").exists, "no 'Use theirs instead' action")
        note("CONFLICT BANNER rendered with both explicit actions present")

        // See what changed.
        if tapButton(containing: "See what changed", label: "diff disclosure") {
            sleep(1)
            shot("08-conflict-diff")
        }

        // Resolve with "Keep my version" — the draft should save, and the
        // OTHER writer's version must not be silently discarded from history
        // (verified separately via GET .../revisions in the report, not by
        // this harness).
        guard tapButton(containing: "Keep my version", label: "Keep my version") else { return }
        let resolvedDismiss = waitUntilGone(app.navigationBars["Edit document"], timeout: 15)
        sleep(2)
        shot("09-conflict-resolved")
        XCTAssertTrue(resolvedDismiss, "the sheet never closed after resolving the conflict with 'Keep my version'")
        XCTAssertTrue(
            app.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] %@", "MY edit, typed while")).firstMatch.waitForExistence(timeout: 8),
            "the kept version is not what is rendered after resolving the conflict"
        )
        note("CONFLICT RESOLVED via Keep my version — my draft is what is now shown")

        print("DOC_EDIT_DONE misses=\(misses.count) \(misses)")
    }

    private func waitUntilGone(_ e: XCUIElement, timeout: TimeInterval) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if !e.exists { return true }
            usleep(300_000)
        }
        return false
    }
}

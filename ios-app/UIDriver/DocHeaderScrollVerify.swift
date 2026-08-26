import XCTest

/// THROWAWAY verification harness for the document-reader status-bar
/// collision fix (ios/doc-header-scroll). Not meant to become a permanent
/// regression test — it exists to produce real on-device screenshots at
/// rest / mid-scroll / at-bottom, which is the only way to actually see
/// whether `.toolbarBackground(.visible, for: .navigationBar)` fixed the
/// title ghosting behind the status bar. Delete after use.
final class DocHeaderScrollVerify: XCTestCase {

    private var app: XCUIApplication!
    private var shotDir: URL!
    private var step = 0

    override func setUpWithError() throws {
        continueAfterFailure = true
        let label = ProcessInfo.processInfo.environment["SHOT_LABEL"] ?? "doc-header"
        let base = URL(fileURLWithPath: "/tmp/emp-shots").appendingPathComponent(label)
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

    private func note(_ s: String) { print("DHS_NOTE \(s)") }
    private func miss(_ s: String) { print("DHS_MISS \(s)") }

    private func reliableTap(_ e: XCUIElement) {
        e.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
    }

    private func signIn() {
        app.launch()
        sleep(2)
        if !SignInFlow.reachEmailForm(app) {
            miss("could not reach the login form from the welcome screen")
        }
        let email = app.textFields.firstMatch
        if email.waitForExistence(timeout: 6) {
            email.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            usleep(500_000)
            email.typeText("ios.verify@example.com")
            let pw = app.secureTextFields.firstMatch
            if pw.waitForExistence(timeout: 4) {
                pw.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                usleep(500_000)
                pw.typeText("iosVerify-2026!")
            }
            let cont = app.buttons["Continue"]
            for _ in 0..<3 {
                cont.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                if app.tabBars.firstMatch.waitForExistence(timeout: 15) { break }
            }
        }
        XCTAssertTrue(app.tabBars.firstMatch.waitForExistence(timeout: 30), "sign-in failed")
        sleep(2)
    }

    @discardableResult
    private func tab(_ index: Int, expect navBar: String) -> Bool {
        let bar = app.tabBars.firstMatch
        guard bar.waitForExistence(timeout: 10), bar.buttons.count > index else {
            miss("tab bar missing for index \(index)"); return false
        }
        for _ in 0..<3 {
            reliableTap(bar.buttons.element(boundBy: index))
            if app.navigationBars[navBar].waitForExistence(timeout: 8) { sleep(1); return true }
            sleep(1)
        }
        miss("tab \(index) did not reach '\(navBar)'")
        return false
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
        e.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        sleep(1)
        return true
    }

    /// A small, controlled upward drag — NOT `app.swipeUp()`, which covers
    /// most of the screen in one gesture and would jump straight past the
    /// exact "header half under the status bar" moment this test exists to
    /// catch on a document this short (704 chars / 29 lines).
    private func dragUp(fraction: CGFloat) {
        let start = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.65))
        let end = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.65 - fraction))
        start.press(forDuration: 0.05, thenDragTo: end)
        usleep(400_000)
    }

    func testDocumentReaderHeaderScroll() throws {
        signIn()

        // ── SECOND REFERENCE — Inbox has a REAL `.navigationTitle("Inbox")`
        // with NO explicit displayMode, i.e. the classic Apple large-title
        // collapse, and its rows scroll DIRECTLY under the bar (no pinned
        // intermediate view the way ProjectDetailView's Picker is). This is
        // the genuine "does content passing under a titled bar on THIS
        // build actually get hidden/blurred" reference — ProjectDetailView
        // does not test that at all, since nothing there ever reaches the
        // bar.
        guard tab(0, expect: "Inbox") else { return }
        for i in 1...4 { dragUp(fraction: 0.09); shot("inbox-reference-scroll-\(i)") }

        guard tab(2, expect: "Projects") else { return }
        guard tapRow("Mobile App") else { miss("no 'Mobile App' project row"); return }

        // ── REFERENCE SCREEN — ProjectDetailView has a REAL
        // .navigationTitle("Mobile App") and 30 tasks to scroll. Captured
        // here, on the SAME build, so it is a true baseline for what a
        // titled screen's nav bar actually renders on this exact iOS/
        // device combination — not an assumption from reading the source.
        for i in 1...3 { dragUp(fraction: 0.09); shot("reference-titled-scroll-\(i)") }
        // Scroll it back to the top before moving on to Documents.
        for _ in 0..<4 { app.swipeDown() }
        usleep(400_000)

        let documentsSegment = app.buttons["Documents"]
        if documentsSegment.waitForExistence(timeout: 6) {
            reliableTap(documentsSegment)
            sleep(1)
        } else {
            miss("no Documents segment on the project screen")
        }

        guard tapRow("Design Notes") else {
            miss("seeded 'Design Notes: Task Board' document not found in Mobile App")
            return
        }
        sleep(1)

        guard app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS[c] %@", "task-board.md")
        ).firstMatch.waitForExistence(timeout: 10) else {
            miss("document did not open — no path text rendered")
            return
        }

        // ── AT REST — header not yet scrolled ───────────────────────────
        shot("at-rest")

        // ── A SEQUENCE of small drags, so whichever one lands the header
        // exactly under the status bar is captured regardless of the
        // precise pixel math on this device/text-size combination. ───────
        for i in 1...4 {
            dragUp(fraction: 0.09)
            shot("mid-scroll-\(i)")
        }

        // ── TO THE BOTTOM — proving content clears the floating tab bar ──
        for _ in 0..<6 { dragUp(fraction: 0.3) }
        sleep(1)
        shot("at-bottom")

        note("DHS_DONE")
    }
}

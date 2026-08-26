import XCTest

/// TEMPORARY, single-purpose. Navigates straight to a task detail screen
/// that carries every PropertyRow kind (Status/Priority/Assignee/Due/
/// Labels) plus a Sub-tasks section with a trailing count, a Description
/// section and an Activity feed — then screenshots it, scrolling down twice
/// to cover the whole page. Run once per `content_size` (set externally via
/// `xcrun simctl ui <udid> content_size ...` before each invocation, and
/// pass a distinct SHOT_LABEL) to compare `large` against
/// `accessibility-extra-extra-extra-large`.
///
/// Reuses the exact navigation path `Supplementary.swift`'s own SUB-TASKS
/// section already proved reachable against this seeded backend: Projects
/// tab -> "Mobile App" -> "Offline cache for the document reader".
final class PropertyRowAX5: XCTestCase {

    private var app: XCUIApplication!
    private var shotDir: URL!
    private var step = 0

    override func setUpWithError() throws {
        continueAfterFailure = true
        let label = ProcessInfo.processInfo.environment["SHOT_LABEL"] ?? "propertyrow-ax5"
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

    private func rows() -> XCUIElementQuery { app.collectionViews.buttons }

    private func type(into field: XCUIElement, _ text: String) {
        field.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        usleep(400_000)
        field.typeText(text)
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
            if !ok && !e.exists { return false }
        }
        for _ in 0..<3 {
            e.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            let deadline = Date().addingTimeInterval(8)
            while Date() < deadline { if waitFor() { sleep(1); return true }; usleep(400_000) }
            sleep(1)
        }
        return false
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
        return false
    }

    func testPropertyRowAX5() throws {
        app.launch(); sleep(2)

        SignInFlow.reachEmailForm(app)
        let email = app.textFields.firstMatch
        if email.waitForExistence(timeout: 4) {
            type(into: email, "ios.verify@example.com")
            let pw = app.secureTextFields.firstMatch
            if pw.waitForExistence(timeout: 4) { type(into: pw, "iosVerify-2026!") }
            if app.buttons["Continue"].exists { app.buttons["Continue"].tap() }
        }
        XCTAssertTrue(app.tabBars.firstMatch.waitForExistence(timeout: 25))
        sleep(3)

        XCTAssertTrue(tab(2, expect: "Projects"), "could not reach Projects tab")
        XCTAssertTrue(
            tapRowByLabel("Mobile App", waitFor: { self.app.navigationBars["Mobile App"].exists }),
            "could not open the 'Mobile App' project"
        )
        XCTAssertTrue(
            tapRowByLabel("Offline cache for the document reader",
                          waitFor: { self.app.textFields["Comment"].exists }),
            "could not open the target task"
        )
        sleep(2)

        // Top: header + the whole Status/Priority/Assignee/Due/Labels card.
        shot("task-top")
        // Scroll to the Sub-tasks header (trailing count) / Description.
        app.swipeUp(); usleep(900_000)
        shot("task-mid")
        // Scroll further to Activity.
        app.swipeUp(); usleep(900_000)
        shot("task-activity")
        app.swipeUp(); usleep(900_000)
        shot("task-activity-2")
    }
}

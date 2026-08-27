import XCTest

/// Live, on-device proof that a document image renders honestly — the ONE
/// thing a unit test cannot show: what actually appears on screen for a
/// working image, a URL that 404s, and a `.svg` policy refuses.
///
/// Screenshots the seeded "iOS Image Policy Verification" document (created
/// via the real API, see MarkdownImageTests.swift's own header for the
/// three URLs it embeds). Run once per appearance
/// (`xcrun simctl ui <udid> appearance light|dark` before each invocation —
/// this harness does not flip appearance itself, matching how
/// `verify-on-device.sh` already sets it before install/launch).
final class MarkdownImageVerify: XCTestCase {

    private var app: XCUIApplication!
    private var shotDir: URL!
    private var step = 0
    private var misses: [String] = []

    override func setUpWithError() throws {
        continueAfterFailure = true
        let label = ProcessInfo.processInfo.environment["SHOT_LABEL"] ?? "markdown-images"
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

    private func miss(_ s: String) { misses.append(s); print("IMG_MISS \(s)") }

    private func reliableTap(_ e: XCUIElement) {
        e.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
    }

    private func type(into field: XCUIElement, _ text: String) {
        for _ in 0..<4 {
            if (field.value(forKey: "hasKeyboardFocus") as? Bool) != true {
                reliableTap(field)
                usleep(700_000)
            }
            if (field.value(forKey: "hasKeyboardFocus") as? Bool) == true { field.typeText(text); return }
            sleep(1)
        }
        miss("could not focus a text field")
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
        reliableTap(e)
        sleep(2)
        return true
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
        return false
    }

    func testDocumentImages() throws {
        app.launch()
        sleep(2)

        switch SignInFlow.reach(app) {
        case .alreadySignedIn:
            break
        case .emailFormReady:
            let email = app.textFields.firstMatch
            if email.waitForExistence(timeout: 6) {
                type(into: email, "ios.verify@example.com")
                let pw = app.secureTextFields.firstMatch
                if pw.waitForExistence(timeout: 4) { type(into: pw, "iosVerify-2026!") }
                let cont = app.buttons["Continue"]
                for _ in 0..<3 {
                    reliableTap(cont)
                    if app.tabBars.firstMatch.waitForExistence(timeout: 15) { break }
                }
            }
        case .neither:
            miss("neither already-signed-in nor the email form appeared")
        }
        XCTAssertTrue(app.tabBars.firstMatch.waitForExistence(timeout: 30), "sign-in failed")
        sleep(2)

        guard tab(2, expect: "Projects") else { return }
        guard tapRow("General") else { miss("no 'General' project row"); return }
        sleep(1)

        let documentsSegment = app.buttons["Documents"]
        if documentsSegment.waitForExistence(timeout: 6) {
            reliableTap(documentsSegment)
            sleep(1)
        } else {
            miss("no Documents segment on the project screen")
        }

        guard tapRow("Image Policy Verification") else {
            miss("seeded verification document not found — was it created in ws_6d2846c045de, project General?")
            return
        }
        sleep(2)
        shot("document")

        // The three states, named so a miss says which one:
        XCTAssertTrue(
            app.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] %@", "Image Rendering Verification")).firstMatch
                .waitForExistence(timeout: 8),
            "document title/heading never rendered"
        )
        if !app.images.firstMatch.waitForExistence(timeout: 15) {
            miss("no loaded image appeared at all (the working-image case)")
        }
        if !app.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] %@", "Couldn't load")).firstMatch
            .waitForExistence(timeout: 15) {
            miss("no 'Couldn't load' failure state appeared (the 404 case)")
        }
        if !app.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] %@", "can't be shown")).firstMatch
            .waitForExistence(timeout: 8) {
            miss("no policy-refused state appeared (the .svg case)")
        }
        // Scroll to be sure the whole document, including the closing
        // paragraph after the third image, is visible in at least one shot.
        app.swipeUp()
        sleep(1)
        shot("document-scrolled")

        if !misses.isEmpty {
            XCTFail("MISSES: \(misses.joined(separator: " | "))")
        }
    }
}

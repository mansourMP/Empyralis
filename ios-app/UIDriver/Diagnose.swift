import XCTest

/// TEMPORARY. Prints the accessibility tree so the walkthrough can be aimed
/// at what actually exists rather than at what a screenshot suggests.
final class Diagnose: XCTestCase {

    func testTree() throws {
        let app = XCUIApplication()
        app.launch()
        sleep(2)

        let getStarted = app.buttons["Get started"]
        if getStarted.waitForExistence(timeout: 6) { getStarted.tap(); sleep(1) }

        let email = app.textFields.firstMatch
        if email.waitForExistence(timeout: 8) {
            email.tap(); email.typeText("ios.verify@example.com")
            let pw = app.secureTextFields.firstMatch
            if pw.waitForExistence(timeout: 3) { pw.tap(); pw.typeText("iosVerify-2026!") }
            let cont = app.buttons["Continue"]
            if cont.exists { cont.tap() }
        }

        let tabBar = app.tabBars.firstMatch
        XCTAssertTrue(tabBar.waitForExistence(timeout: 25))
        sleep(4)

        print("=====TREE:INBOX=====")
        print(app.debugDescription)
        print("=====END=====")

        print("TABBAR button count: \(tabBar.buttons.count)")
        for i in 0..<tabBar.buttons.count {
            let b = tabBar.buttons.element(boundBy: i)
            print("TAB[\(i)] id=\(b.identifier) label=\(b.label) hittable=\(b.isHittable) frame=\(b.frame)")
        }

        // Try switching by index rather than label.
        if tabBar.buttons.count > 2 {
            tabBar.buttons.element(boundBy: 2).tap()
            sleep(3)
            print("=====TREE:AFTER-INDEX2-TAP=====")
            print(app.debugDescription)
            print("=====END=====")
        }
    }
}

import XCTest

/// Verifies TASK AUTHORING end to end against the real seeded backend:
/// create a task, create a sub-task, edit a title, edit a description.
/// Every string carries a run-specific MARKER so the host can grep the real
/// `GET /fleet/tasks` response afterward and confirm each write actually
/// landed on the server, rather than trusting the optimistic UI alone.
final class TaskAuthoring: XCTestCase {

    private var app: XCUIApplication!
    private var shotDir: URL!
    private var step = 0
    private var misses: [String] = []

    /// Unique per run so repeated runs never collide with each other's rows.
    private let marker = "UID\(Int(Date().timeIntervalSince1970))"

    override func setUpWithError() throws {
        continueAfterFailure = true
        let label = ProcessInfo.processInfo.environment["SHOT_LABEL"] ?? "task-authoring"
        let base = URL(fileURLWithPath: "/tmp/emp-shots").appendingPathComponent(label)
        try? FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        shotDir = base
        app = XCUIApplication()
    }

    // MARK: - plumbing (same idioms as VerificationGaps.swift)

    private func shot(_ name: String) {
        step += 1
        let png = XCUIScreen.main.screenshot().pngRepresentation
        let file = shotDir.appendingPathComponent(String(format: "%02d-%@.png", step, name))
        try? png.write(to: file)
        print("SHOT \(file.path)")
    }

    private func miss(_ s: String) { misses.append(s); print("TA_MISS \(s)") }
    private func note(_ s: String) { print("TA_NOTE \(s)") }

    private func hasFocus(_ e: XCUIElement) -> Bool {
        (e.value(forKey: "hasKeyboardFocus") as? Bool) ?? false
    }

    private func focus(_ field: XCUIElement, biasRight: Bool = false) {
        let dx: CGFloat = biasRight ? 0.92 : 0.5
        for _ in 0..<4 {
            if !hasFocus(field) {
                field.coordinate(withNormalizedOffset: CGVector(dx: dx, dy: 0.5)).tap()
                usleep(700_000)
            }
            if hasFocus(field) { return }
            sleep(1)
        }
        miss("could not focus a text field")
    }

    private func type(into field: XCUIElement, _ text: String) {
        focus(field)
        if hasFocus(field) { field.typeText(text) }
    }

    /// Replaces whatever text is already in `field`. Taps near the field's
    /// RIGHT edge to bias the cursor toward the end of the existing text,
    /// then backspaces `oldLength` (+ margin) characters before typing the
    /// replacement — the standard XCUITest trick for a field with no
    /// built-in "clear" affordance.
    private func replaceText(in field: XCUIElement, oldLength: Int, with newText: String) {
        focus(field, biasRight: true)
        guard hasFocus(field) else { return }
        let clear = String(repeating: XCUIKeyboardKey.delete.rawValue, count: oldLength + 8)
        field.typeText(clear)
        field.typeText(newText)
    }

    private func signIn() {
        app.launch()
        sleep(2)
        // "Get started" opens the system Safari sheet, which this harness
        // cannot drive — go in through "Sign in with email instead".
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

    /// Task detail has no navigation title of its own; the comment composer
    /// (present nowhere else) is the proof of arrival.
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

    /// EXACT label match — safe only for a button with a plain-text label
    /// or an explicit `.accessibilityLabel` override (no icon+text
    /// composite). See `tapContaining` for the composite case.
    @discardableResult
    private func tapExact(_ label: String, timeout: TimeInterval = 8) -> Bool {
        let b = app.buttons[label]
        guard b.waitForExistence(timeout: timeout) else { miss("no '\(label)' button"); return false }
        // Re-check BEFORE each retry. The blind 3x loop this replaced threw
        // "Failed to get matching snapshot" on a button that had worked
        // perfectly: `Create task` renames itself to `Creating…` the instant
        // it is pressed, so retry #2 queried a label that no longer existed
        // and the harness reported a failure for a tap that had LANDED.
        //
        // A control that renames itself while it works is honest UI, not a
        // defect — so the harness has to tolerate it. Losing the element
        // after a tap is treated as SUCCESS, because that is what a button
        // which did its job looks like from out here.
        tapFrameOf(b)
        return true
    }

    /// A `PickerRow`-style composite (icon + text) never matches on an EXACT
    /// label — see VerificationGaps.swift's own note on this. Matched on
    /// CONTAINS instead.
    @discardableResult
    private func tapContaining(_ text: String, timeout: TimeInterval = 8) -> Bool {
        let b = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] %@", text)).firstMatch
        guard b.waitForExistence(timeout: timeout) else {
            // Dump what IS on screen rather than guessing at why the query
            // missed. A control that is plainly visible in a screenshot and
            // still unmatchable means the accessibility tree disagrees with
            // the render — and the only way to tell which is to look.
            let labels = app.buttons.allElementsBoundByIndex.map { "\($0.label)|id=\($0.identifier)" }
            print("TREE_BUTTONS \(labels)")
            print("TREE_OTHER \(app.staticTexts.allElementsBoundByIndex.prefix(30).map(\.label))")
            miss("no button containing '\(text)'")
            return false
        }
        // Re-check BEFORE each retry. The blind 3x loop this replaced threw
        // "Failed to get matching snapshot" on a button that had worked
        // perfectly: `Create task` renames itself to `Creating…` the instant
        // it is pressed, so retry #2 queried a label that no longer existed
        // and the harness reported a failure for a tap that had LANDED.
        //
        // A control that renames itself while it works is honest UI, not a
        // defect — so the harness has to tolerate it. Losing the element
        // after a tap is treated as SUCCESS, because that is what a button
        // which did its job looks like from out here.
        tapFrameOf(b)
        return true
    }


    /// Tap an element by CAPTURING ITS FRAME FIRST, then hitting that
    /// absolute screen point — never by re-resolving the element query at
    /// tap time.
    ///
    /// Three harness failures in a row came from the obvious shape
    /// (`element.coordinate(...).tap()` in a small retry loop), and the
    /// reason is worth writing down: on this screen the primary button
    /// RENAMES ITSELF the instant it is pressed (`Create task` ->
    /// `Creating…`), so any query keyed on its label stops matching mid-tap
    /// and XCUITest reports "Failed to get matching snapshot" for a tap that
    /// had already landed. Even an `.exists` guard races, because `.exists`
    /// and `.coordinate()` resolve the query twice.
    ///
    /// A control that renames itself while it works is honest UI, not a
    /// defect — the harness is what has to accommodate it. A screen point
    /// needs no element to still be there.
    private func tapFrameOf(_ element: XCUIElement) {
        let f = element.frame
        guard f.width > 0, f.height > 0 else {
            element.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            return
        }
        app.coordinate(withNormalizedOffset: .zero)
            .withOffset(CGVector(dx: f.midX, dy: f.midY))
            .tap()
        usleep(600_000)
    }

    private func waitForSheetGone(_ navTitle: String, timeout: TimeInterval = 10) {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline && app.navigationBars[navTitle].exists {
            usleep(300_000)
        }
    }

    // MARK: - The one flow: create task -> create sub-task -> edit title -> edit description

    func testCreateEditFlow() throws {
        signIn()

        // ---- 1. CREATE A TASK, from Projects -> Mobile App -> Tasks -----
        guard tab(2, expect: "Projects") else { return }
        guard tapRow("Mobile App", expect: "Mobile App") else { return }
        sleep(1)
        shot("01-project-tasks")

        let taskTitle = "\(marker) create test"
        guard tapExact("New task") else { return }
        shot("02-new-task-sheet")

        let titleField = app.textFields["Task title"]
        guard titleField.waitForExistence(timeout: 6) else { miss("no Task title field"); return }
        type(into: titleField, taskTitle)
        shot("03-new-task-typed")

        // CONTAINS, not exact. The button is plainly on screen (captured in
        // 03-new-task-typed.png) and `app.buttons["Create task"]` still
        // matched nothing — the query resolves against IDENTIFIERS, and this
        // button carries only a label. Same class of miss this file's own
        // `tapContaining` note already documents for composite rows.
        guard tapContaining("Create task") else { return }
        waitForSheetGone("New task")
        sleep(2)
        shot("04-after-create")
        note("CREATED task titled '\(taskTitle)'")

        // ---- 2. Open it, then CREATE A SUB-TASK --------------------------
        guard openTask(taskTitle) else { return }
        shot("05-task-opened")

        let subtaskTitle = "\(marker) subtask test"
        guard tapContaining("Add sub-task") else { return }
        shot("06-new-subtask-sheet")

        let subtaskField = app.textFields["Task title"]
        guard subtaskField.waitForExistence(timeout: 6) else { miss("no sub-task title field"); return }
        type(into: subtaskField, subtaskTitle)
        guard tapContaining("Create task") else { return }
        waitForSheetGone("New sub-task")
        sleep(2)
        shot("07-after-subtask-create")
        note("CREATED sub-task titled '\(subtaskTitle)'")

        // Assert on VISIBLE TEXT, not on rows(). rows() is
        // `app.collectionViews.buttons`, and TaskDetailView is a ScrollView —
        // so that query cannot match a sub-task row that is plainly on screen
        // (07-after-subtask-create.png shows it, with its own MOB-20 key,
        // under a correctly-updated "SUB-TASKS 0/1" header). The original
        // assertion was reporting an app defect that did not exist.
        let subtaskText = app.staticTexts
            .matching(NSPredicate(format: "label CONTAINS[c] %@", subtaskTitle)).firstMatch
        XCTAssertTrue(subtaskText.waitForExistence(timeout: 8),
                      "the new sub-task never appeared under Sub-tasks")

        // ---- 3. EDIT THE TITLE --------------------------------------------
        let newTitle = "\(marker) EDITED title"
        let headerButton = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] %@", taskTitle)).firstMatch
        guard headerButton.waitForExistence(timeout: 6) else { miss("no tappable title header"); return }
        tapFrameOf(headerButton)
        guard app.navigationBars["Edit title"].waitForExistence(timeout: 6) else {
            miss("title edit sheet never opened"); return
        }
        shot("08-edit-title-sheet")

        let titleEditField = app.textFields.firstMatch
        guard titleEditField.waitForExistence(timeout: 4) else { miss("no title edit field"); return }
        replaceText(in: titleEditField, oldLength: taskTitle.count, with: newTitle)
        shot("09-title-retyped")

        guard tapExact("Save") else { return }
        waitForSheetGone("Edit title")
        sleep(2)
        shot("10-after-title-save")
        note("RENAMED task to '\(newTitle)'")

        XCTAssertTrue(
            app.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] %@", newTitle)).firstMatch.waitForExistence(timeout: 8),
            "the edited title never rendered on screen"
        )

        // ---- 4. EDIT THE DESCRIPTION ---------------------------------------
        let descText = "\(marker) description body"
        let descButton = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] %@", "Add a description")).firstMatch
        guard descButton.waitForExistence(timeout: 6) else { miss("no description row"); return }
        descButton.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        guard app.navigationBars["Edit description"].waitForExistence(timeout: 6) else {
            miss("description edit sheet never opened"); return
        }
        shot("11-edit-description-sheet")

        let descField = app.textViews.firstMatch
        guard descField.waitForExistence(timeout: 4) else { miss("no description text view"); return }
        descField.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        usleep(500_000)
        descField.typeText(descText)
        shot("12-description-typed")

        guard tapExact("Save") else { return }
        waitForSheetGone("Edit description")
        sleep(2)
        shot("13-after-description-save")
        note("SET description to '\(descText)'")

        XCTAssertTrue(
            app.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] %@", descText)).firstMatch.waitForExistence(timeout: 8),
            "the edited description never rendered on screen"
        )

        // Printed so the host can grep the real API response for this exact
        // marker afterward and confirm title/description/parent_task_id
        // landed on the server, not just in the optimistic UI.
        print("TASK_AUTHORING_MARKER \(marker)")
        print("TASK_AUTHORING_DONE misses=\(misses.count) \(misses)")
    }
}

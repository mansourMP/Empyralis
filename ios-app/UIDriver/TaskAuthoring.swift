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

    /// Every miss dumps state — frames, hittability, the button labels
    /// actually present — so a failure produces EVIDENCE instead of a guess.
    /// `probe` is the specific element the caller was trying to reach, when
    /// there is one; its frame/hittability is the single most useful fact on
    /// a tap that silently landed wrong.
    private func miss(_ s: String, probe: XCUIElement? = nil) {
        misses.append(s)
        print("TA_MISS \(s)")
        dumpTree("miss\(misses.count)", probe: probe)
    }
    private func note(_ s: String) { print("TA_NOTE \(s)") }

    /// Ported from DocumentEditVerify.swift's own `dumpTree` — a screenshot
    /// plus every nav-bar/button/text label on screen, plus the full
    /// accessibility tree to a scratch file, plus (when given) the exact
    /// probe element's frame/hittability. This is what turns "step N missed
    /// on iPhone 13, cause unknown" into an actual diagnosis instead of a
    /// second blind guess.
    private func dumpTree(_ label: String, probe: XCUIElement? = nil) {
        shot(label)
        let navBars = app.navigationBars.allElementsBoundByIndex.map(\.identifier)
        note("TREE[\(label)] navBars=\(navBars)")
        let buttons = app.buttons.allElementsBoundByIndex.prefix(40)
            .map { "'\($0.label)' hittable=\($0.isHittable) frame=\($0.frame)" }
        note("TREE[\(label)] buttons=\(buttons)")
        let texts = app.staticTexts.allElementsBoundByIndex.prefix(25).map(\.label)
        note("TREE[\(label)] staticTexts=\(texts)")
        if let probe {
            note("TREE[\(label)] PROBE exists=\(probe.exists) hittable=\(probe.isHittable) frame=\(probe.frame)")
        }
        let full = app.debugDescription
        let file = shotDir.appendingPathComponent("\(label)-tree.txt")
        try? full.write(to: file, atomically: true, encoding: .utf8)
        note("TREE[\(label)] full dump at \(file.path)")
    }

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

    /// ROOT CAUSE, found live on iPhone 13 (a crash, not a miss): the
    /// previous body here re-tapped `e.coordinate(...)` inside a 3x retry
    /// loop. When the FIRST tap already navigated away successfully, `e` —
    /// a row from the LIST screen — no longer exists on the new screen, and
    /// a slow render (device- or backend-load-dependent) can make the
    /// post-tap wait outlast its timeout even on a tap that worked. The next
    /// loop iteration then re-resolves `e.coordinate(...)` against a query
    /// that matches nothing and XCUITest THROWS
    /// ("Failed to get matching snapshot") instead of the miss() this
    /// function is supposed to report — exactly the rename-mid-tap class of
    /// bug `tapFrameOf`/`settledFrame` were built to close elsewhere in this
    /// file, just never applied here. Fixed the same way: ONE tap via a
    /// settled, captured frame, then a genuinely generous wait. A retry is
    /// still allowed, but ONLY when the row still exists — i.e. the tap
    /// genuinely missed, not merely a slow transition after a real hit.
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
        for attempt in 0..<2 {
            tapFrameOf(e)
            guard let navBar else { sleep(2); return true }
            if app.navigationBars[navBar].waitForExistence(timeout: 15) { sleep(1); return true }
            if attempt == 0 && !e.exists { break } // navigated away; a slow render, not a miss
        }
        miss("row '\(text)' never reached '\(navBar ?? "")'", probe: e)
        return false
    }

    /// Task detail has no navigation title of its own; the comment composer
    /// (present nowhere else) is the proof of arrival. See `tapRow`'s header
    /// comment — this had the identical re-resolve-a-gone-element crash risk
    /// and is fixed the same way.
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
        for attempt in 0..<2 {
            tapFrameOf(e)
            if app.textFields["Comment"].waitForExistence(timeout: 15) { sleep(2); return true }
            if attempt == 0 && !e.exists { break } // navigated away; a slow render, not a miss
        }
        miss("task '\(text)' never opened", probe: e)
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
        //
        // tapFrameOfVerified, not tapFrameOf: a settled, accurate,
        // hittable frame can STILL produce a synthetic tap that simply
        // never registers (reproduced live — see tapFrameOfVerified's own
        // header). One retry, conditioned on the button being unchanged.
        tapFrameOfVerified(b)
        return true
    }

    /// A `PickerRow`-style composite (icon + text) never matches on an EXACT
    /// label — see VerificationGaps.swift's own note on this. Matched on
    /// CONTAINS instead.
    @discardableResult
    private func tapContaining(_ text: String, timeout: TimeInterval = 8) -> Bool {
        let b = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] %@", text)).firstMatch
        guard b.waitForExistence(timeout: timeout) else {
            miss("no button containing '\(text)'")
            return false
        }
        // `.exists` is satisfied the instant a plain (non-lazy) ScrollView
        // instantiates a child, REGARDLESS of scroll position — TaskDetailView
        // is exactly that shape (ScrollView { VStack { header, properties,
        // description, subtasks, ... } }), so a row below the fold "exists"
        // from the first frame and a blind tapFrameOf lands on whatever
        // screen point its OFF-SCREEN frame happens to report. isHittable is
        // the real question; scroll toward it before trusting the frame.
        if !b.isHittable {
            note("'\(text)' exists but is not yet hittable (frame=\(b.frame)) — scrolling into view")
            scrollIntoView(b)
            if !b.isHittable {
                note("'\(text)' still not hittable after scrolling (frame=\(b.frame))")
            }
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
        //
        // tapFrameOfVerified, not tapFrameOf — see tapExact's own note.
        tapFrameOfVerified(b)
        return true
    }

    /// Scrolls the main scroll view until `element` is hittable, or gives up
    /// after a bounded number of swipes. Mirrors the swipe-and-recheck shape
    /// `tapRow`/`openTask` already use for lazily-loaded collection rows —
    /// this is the same idea applied to a plain, eagerly-instantiated
    /// ScrollView, where the element was never "not found," only off-screen.
    @discardableResult
    private func scrollIntoView(_ element: XCUIElement) -> Bool {
        if element.exists && element.isHittable { return true }
        let scroller = app.scrollViews.firstMatch
        guard scroller.exists else { return element.exists && element.isHittable }
        for _ in 0..<10 {
            if element.exists && element.isHittable { return true }
            scroller.swipeUp()
            usleep(350_000)
        }
        return element.exists && element.isHittable
    }

    /// An element's frame, once it has STOPPED MOVING.
    ///
    /// `tapFrameOf` captures a frame and taps that screen point, which is what
    /// made it immune to the rename-mid-tap race. But it introduced a second,
    /// subtler one: a frame captured while a navigation push is still
    /// animating is where the control WAS, not where it is, so the tap lands
    /// on empty space and the control silently never fires.
    ///
    /// That is precisely the failure that made the device matrix look like a
    /// layout bug — the toolbar "+" is animating in from the right when a
    /// project pushes, so whether the tap lands depends on how fast the
    /// device finishes the transition. iPhone 16 and 17 Pro were reliably
    /// quick enough; 13 and 14 Pro were not, intermittently, and the symptom
    /// (a sheet that never opens) is indistinguishable from a dead button.
    ///
    /// So: sample until two consecutive reads agree, then tap.
    ///
    /// 20 iterations (4s), not the original 10 (2s) — CLAUDE.md's own device
    /// matrix records iPhone 13/14 Pro as measurably SLOWER simulators on
    /// this host, and a fixed 2s window that is plenty on 16/17 Pro is
    /// exactly the kind of budget that would silently fall through here
    /// without ever reporting it: a fall-through returns whatever the LAST
    /// read was, which may still be mid-animation, with no signal that
    /// settling was never actually confirmed.
    private func settledFrame(of element: XCUIElement) -> CGRect {
        var previous = element.frame
        for _ in 0..<20 {
            usleep(200_000)
            let current = element.frame
            if current == previous && current.width > 0 { return current }
            previous = current
        }
        note("settledFrame never converged after 4s, tapping last-read frame=\(previous)")
        return previous
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
        let f = settledFrame(of: element)
        guard f.width > 0, f.height > 0 else {
            element.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            return
        }
        app.coordinate(withNormalizedOffset: .zero)
            .withOffset(CGVector(dx: f.midX, dy: f.midY))
            .tap()
        usleep(600_000)
    }

    /// `tapFrameOf`, but retried ONCE if the tap plainly did nothing.
    ///
    /// Reproduced live on iPhone 13, run 2: a task's title-header button —
    /// accurate frame, `hittable == true`, nothing overlapping it — was
    /// tapped via `tapFrameOf` and NOTHING happened. Confirmed by MD5: the
    /// screenshot taken immediately after the failed wait was byte-for-byte
    /// identical to the one taken before the tap. `tapFrameOf` itself has no
    /// retry — one settled-frame tap, once — so a synthetic touch that
    /// simply doesn't register (an XCUITest-level flake, not an app bug: the
    /// same tap mechanism worked for every other button in the same run)
    /// fails the whole test with no second attempt.
    ///
    /// The retry condition mirrors `tapRow`/`openTask`'s own fix: if the
    /// tapped element is STILL hittable shortly after, nothing changed as a
    /// result of the tap (no sheet covered it, no navigation occurred, no
    /// label change) — that is the generic signal a miss actually happened,
    /// as opposed to a slow transition after a tap that DID land, where the
    /// element would already be covered/gone.
    private func tapFrameOfVerified(_ element: XCUIElement) {
        for attempt in 0..<2 {
            tapFrameOf(element)
            usleep(400_000)
            if attempt == 0 && element.exists && element.isHittable {
                note("tap on '\(element.label)' left it unchanged (still hittable) — retrying once")
                continue
            }
            return
        }
    }

    /// Waits for a sheet's own nav bar to disappear. Previously silent on
    /// timeout — a sheet that never dismissed (e.g. a create request that
    /// genuinely took longer than the wait, on a shared backend under
    /// concurrent load from other agents' runs) would fall through with NO
    /// signal, and every following step would then fail against the wrong
    /// screen with a confusing, unrelated-looking miss. Now reports which
    /// happened, so a real timeout here is distinguishable from whatever
    /// broke two steps later.
    @discardableResult
    private func waitForSheetGone(_ navTitle: String, timeout: TimeInterval = 10) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline && app.navigationBars[navTitle].exists {
            usleep(300_000)
        }
        let gone = !app.navigationBars[navTitle].exists
        if !gone {
            miss("sheet '\(navTitle)' did not dismiss within \(Int(timeout))s")
        }
        return gone
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
        // NewTaskSheet is deliberately NOT optimistic (its own header
        // comment: "waits for a real, server-minted task") — dismissal
        // waits on a real network round trip, which is genuinely slower on
        // a shared disposable backend under concurrent load. 20s, not the
        // 10s default that is correct for the optimistic edit sheets below.
        waitForSheetGone("New task", timeout: 20)
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
        // Same non-optimistic create path as above.
        waitForSheetGone("New sub-task", timeout: 20)
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
        // The header is the FIRST section on TaskDetailView, so it is almost
        // always already hittable — but a sub-task row just got inserted
        // above it in the accessibility tree via the previous step's
        // assertion re-query, and a fresh navigation can still land mid-
        // scroll on a slow device. Cheap insurance, same as every other tap
        // in this file now gets.
        if !headerButton.isHittable { scrollIntoView(headerButton) }
        tapFrameOfVerified(headerButton)
        guard app.navigationBars["Edit title"].waitForExistence(timeout: 6) else {
            miss("title edit sheet never opened", probe: headerButton); return
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
        // The description row sits BELOW the header + properties card
        // (Status/Priority/Assignee/Due/Labels) in TaskDetailView's plain,
        // non-lazy ScrollView — `.exists` is satisfied from the first frame
        // regardless of scroll position, so a tap keyed only on `.exists`
        // can land on a frame that is genuinely off-screen. Scroll toward it
        // first; tapFrameOf's own settling still applies afterward.
        if !descButton.isHittable {
            note("description row exists but not hittable (frame=\(descButton.frame)) — scrolling into view")
            scrollIntoView(descButton)
        }
        tapFrameOfVerified(descButton)
        guard app.navigationBars["Edit description"].waitForExistence(timeout: 6) else {
            miss("description edit sheet never opened", probe: descButton); return
        }
        shot("11-edit-description-sheet")

        let descField = app.textViews.firstMatch
        guard descField.waitForExistence(timeout: 4) else { miss("no description text view"); return }
        // Focus-and-verify, same as every text field in this file — a bare
        // tap+typeText (the shape this replaced) has no confirmation that
        // the tap actually landed focus before typing starts, and this
        // harness has repeatedly seen a tap resolve/report-hittable/no-op
        // (README's own documented trap). `type(into:)` retries the tap and
        // checks `hasKeyboardFocus` before committing to `typeText`.
        type(into: descField, descText)
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

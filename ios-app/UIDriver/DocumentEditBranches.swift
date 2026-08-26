import XCTest

/// Live, on-device proof for the FOUR document-editing branches that shipped
/// alongside `DocumentEditSheet`/`DocumentWriteAPI`/`DocumentDraftStore` but
/// were never exercised: "Use theirs instead" on a 409 conflict, the
/// background-and-restore local draft, WORKSPACE-viewer gating of the edit
/// pencil, and the `.decoding` / `.unauthorized` outcomes of `performSave()`.
/// Sibling to `DocumentEditVerify.swift` (happy path + "Keep my version",
/// already verified live) — this file covers exactly what that one left
/// unexercised, per its own header.
///
/// THREE DIFFERENT MECHANISMS MAKE SOMETHING HAPPEN ON THE HOST, and each is
/// used because it is the cheapest one that is actually TRUE to what it is
/// proving — none of them is the `hostDo` two-file handshake
/// `VerificationGaps.swift`/`DocumentEditVerify.swift` use, because none of
/// these three needs a SEPARATE watcher process:
///
///   1. A CONFLICTING SERVER-SIDE EDIT (testUseTheirsInsteadDiscards...) is
///      a plain HTTP PATCH. The XCUITest bundle is ordinary host-side Swift
///      code with full network access — the Simulator shares the Mac's own
///      loopback, which is WHY `APIConfig.baseURL`'s DEBUG default
///      (`127.0.0.1:8001`) has always worked from inside it — so the test
///      makes the conflicting write itself rather than asking a separate
///      process to do it on request.
///   2. BACKGROUND + RESTORE (testBackgroundAndRestoreDraft) needs no host
///      help at all: `XCUIDevice.shared.press(.home)` + `app.terminate()` +
///      a fresh `app.launch()` genuinely IS the scenario (a suspended app
///      the OS reclaimed, then relaunched), driven entirely through
///      XCUIApplication's own lifecycle API.
///   3. `.unauthorized` / `.decoding` (testUnauthorizedAndDecodingOnSave)
///      need the APP's OWN network call to receive a response the real
///      backend would never send for a healthy request — that requires
///      sitting BETWEEN the app and the backend, which only a reverse proxy
///      can do. `doc-fault-proxy.py` (run separately, alongside that one
///      test — NOT committed, matching this file family's own convention of
///      not checking in throwaway host scripts; see the header note below
///      for exactly what it must do to be reproduced) forwards everything to
///      the real backend UNCHANGED except one PATCH route, and the test
///      flips its mode with a plain HTTP call to the proxy's own control
///      endpoint — again, the test bundle can just make the call itself.
///
/// `doc-fault-proxy.py`'s contract, so it can be rebuilt from this comment
/// alone: a threaded HTTP server on `127.0.0.1:8098` forwarding every
/// request to `127.0.0.1:8001` byte-for-byte UNCHANGED (status, headers,
/// body), except:
///   - `GET /__mode__/<normal|force_401|force_decoding>` sets an in-process
///     mode flag and answers 200 directly (never forwarded).
///   - while mode is `force_401`, a `PATCH` whose path contains
///     `/fleet/documents/` answers 401 directly (any JSON body — the client
///     only reads the status code for this branch).
///   - while mode is `force_decoding`, that same PATCH answers HTTP 200 with
///     a body that is NOT valid JSON (the client must fail to decode
///     `DocumentResponse` from a 2xx to reach `.decoding`).
///   - mode `normal` (the default) never intercepts anything.
/// Point the app at it for one launch via
/// `EMPYRALIS_API_BASE_URL=http://127.0.0.1:8098/api` — the same override
/// mechanism `FailureStates.swift` already established for simulating a
/// specific network condition without touching source.
final class DocumentEditBranches: XCTestCase {

    private var app: XCUIApplication!
    private var shotDir: URL!
    private var step = 0
    private var misses: [String] = []

    // MARK: - seeded fixtures (this workspace/document, per the task brief)

    private static let workspaceId = "ws_6d2846c045de"
    private static let documentId = "doc_629399cdb6094a13"
    private static let backendBase = "http://127.0.0.1:8001/api"
    private static let proxyControlBase = "http://127.0.0.1:8098"
    private static let proxyAPIBase = "http://127.0.0.1:8098/api"

    private static let ownerEmail = "ios.verify@example.com"
    private static let ownerPassword = "iosVerify-2026!"
    /// A REAL account, created for this task through the real signup +
    /// invite + accept HTTP flow (never by editing the database directly —
    /// see this file's own commit for the exact three curl calls), invited
    /// at workspace role `viewer`. `DocumentAuthoring.canWrite` refuses
    /// anything below `member`, so this is the one account in this
    /// workspace guaranteed to fail that check.
    private static let viewerEmail = "ios.viewer.docs@example.com"
    private static let viewerPassword = "iosViewerDocs-2026!"

    override func setUpWithError() throws {
        continueAfterFailure = true
        let label = ProcessInfo.processInfo.environment["SHOT_LABEL"] ?? "doc-branches"
        let base = URL(fileURLWithPath: "/tmp/emp-shots").appendingPathComponent(label)
        try? FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        shotDir = base
        app = XCUIApplication()
    }

    // MARK: - plumbing (same pattern DocumentEditVerify.swift already uses)

    private func shot(_ name: String) {
        step += 1
        let png = XCUIScreen.main.screenshot().pngRepresentation
        let file = shotDir.appendingPathComponent(String(format: "%02d-%@.png", step, name))
        try? png.write(to: file)
        print("SHOT \(file.path)")
    }

    /// Every call site here is a genuinely blocking condition (a step the
    /// rest of the test depends on did not happen) — so this also fails the
    /// test, not just logs. Without this, a `guard ... else { miss(...);
    /// return }` early-return let a real navigation failure end the test
    /// with NO assertion ever having run, and XCTest reported the whole
    /// thing "passed" — a false "worked" is worse than a red test, per this
    /// codebase's own standing outcome-honesty law. `continueAfterFailure =
    /// true` means this does not stop execution, so later diagnostics still
    /// run; it only stops the run from being reported clean when it wasn't.
    private func miss(_ s: String) {
        misses.append(s)
        print("DOCB_MISS \(s)")
        XCTFail(s)
    }
    private func note(_ s: String) { print("DOCB_NOTE \(s)") }

    private func dumpTree(_ label: String) {
        shot(label)
        let navBars = app.navigationBars.allElementsBoundByIndex.map(\.identifier)
        note("TREE[\(label)] navBars=\(navBars)")
        let buttons = app.buttons.allElementsBoundByIndex.prefix(24).map(\.label)
        note("TREE[\(label)] buttons(first 24)=\(buttons)")
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

    /// A plain `.tap()` has repeatedly resolved, reported hittable, and
    /// silently done nothing in this harness (README's own documented
    /// gotcha) — every tap in this file goes through this instead.
    private func reliableTap(_ e: XCUIElement) {
        e.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
    }

    private func waitUntilGone(_ e: XCUIElement, timeout: TimeInterval) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if !e.exists { return true }
            usleep(300_000)
        }
        return false
    }

    /// `DocumentEditSheet`'s own conflict actions are two stacked `Text`s
    /// (title + consequence) inside one Button, so the real accessibility
    /// label is their concatenation — CONTAINS is the only reliable match,
    /// same lesson `VerificationGaps.pick(_:inSheet:)` already learned.
    private func button(containing text: String) -> XCUIElement {
        app.buttons.matching(NSPredicate(format: "label CONTAINS[c] %@", text)).firstMatch
    }

    private func staticText(containing text: String) -> XCUIElement {
        app.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] %@", text)).firstMatch
    }

    private func rows() -> XCUIElementQuery { app.collectionViews.buttons }

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

    // MARK: - launch / sign-in / sign-out

    /// Launches fresh. `extraEnv` is applied BEFORE `launch()`, matching
    /// `FailureStates.swift`'s own established use of `launchEnvironment`
    /// for `EMPYRALIS_API_BASE_URL` — the only way to point a DEBUG build
    /// somewhere other than its compiled-in default.
    private func launchFresh(extraEnv: [String: String] = [:]) {
        for (key, value) in extraEnv { app.launchEnvironment[key] = value }
        app.launch()
        sleep(2)
    }

    /// Signs OUT first if anything is signed in, then signs in as exactly
    /// the given account — deterministic regardless of which account a
    /// PREVIOUS test in this file left active. `SignInFlow.reachEmailForm`
    /// alone is not enough for this: its own header states plainly that
    /// "already signed in" is one of the states it treats as success, so it
    /// would silently leave a stale session in place rather than switching
    /// accounts.
    private func signInAs(email: String, password: String) {
        if app.tabBars.firstMatch.waitForExistence(timeout: 4) {
            signOutCurrentUser()
        }
        guard SignInFlow.reachEmailForm(app) else {
            miss("could not reach the login form for \(email)"); return
        }
        let emailField = app.textFields.firstMatch
        guard emailField.waitForExistence(timeout: 8) else { miss("no email field for \(email)"); return }
        type(into: emailField, email)
        let pw = app.secureTextFields.firstMatch
        if pw.waitForExistence(timeout: 4) { type(into: pw, password) }
        let cont = app.buttons["Continue"]
        for _ in 0..<3 {
            reliableTap(cont)
            if app.tabBars.firstMatch.waitForExistence(timeout: 15) { break }
        }
        XCTAssertTrue(app.tabBars.firstMatch.waitForExistence(timeout: 30), "sign-in failed for \(email)")
        sleep(3)
    }

    /// Reaches Settings via the Inbox tab's gear and taps "Sign Out" — the
    /// reliable way to reach the login form on an install that has already
    /// passed the once-per-install welcome screen (`Supplementary.swift`'s
    /// own established pattern for switching accounts).
    private func signOutCurrentUser() {
        guard tab(0, expect: "Inbox") else { return }
        let gear = app.buttons["Account and settings"]
        guard gear.waitForExistence(timeout: 8) else { miss("no settings gear to sign out through"); return }
        var settingsOpen = false
        for _ in 0..<3 {
            reliableTap(gear)
            if app.navigationBars["Settings"].waitForExistence(timeout: 6) { settingsOpen = true; break }
            sleep(1)
        }
        guard settingsOpen else { miss("Settings did not open"); return }
        let signOut = app.buttons["Sign Out"]
        guard signOut.waitForExistence(timeout: 5) else { miss("no Sign Out button"); return }
        for _ in 0..<3 {
            reliableTap(signOut)
            if !app.tabBars.firstMatch.exists { break }
            sleep(1)
        }
        sleep(2)
    }

    // MARK: - navigation to the seeded verify document

    /// General -> Documents -> "iOS Edit Verify". Leaves the phone on the
    /// document DETAIL view (not the editor) — callers that need the
    /// editor open still tap the pencil themselves, since whether it even
    /// EXISTS is part of what several of these tests check.
    @discardableResult
    private func navigateToVerifyDocument() -> Bool {
        guard tab(2, expect: "Projects") else { dumpTree("nav-no-projects-tab"); return false }

        // `tapRow` (this file's own copy of the house pattern) taps ONCE and
        // reports success unconditionally -- it never confirms the tap
        // actually navigated anywhere. That has now shown the same
        // swallowed-tap flakiness as Save/Cancel/the pencil, and it is WORSE
        // here: a run right after `backgroundTerminateRelaunch()` hit it,
        // `navigateToVerifyDocument` returned false, the caller's own
        // `guard ... else { return }` ended the test right there, and
        // XCTest reported the whole test "passed" -- no assertion ever ran
        // to FAIL, so a real gap in coverage looked like a clean result.
        // This loop verifies the nav bar actually changed before trusting
        // the tap, retrying the tap itself rather than `tapRow`'s own
        // swipe-and-hope retry (which never re-checks the nav bar either).
        var enteredGeneral = false
        for _ in 0..<4 {
            tapRow("General")
            if app.navigationBars["General"].waitForExistence(timeout: 4) { enteredGeneral = true; break }
        }
        guard enteredGeneral else {
            miss("tapping 'General' never actually navigated into the project")
            dumpTree("nav-general-tap-did-not-navigate")
            return false
        }
        sleep(1)
        dumpTree("nav-after-general")

        let documentsSegment = app.buttons["Documents"]
        guard documentsSegment.waitForExistence(timeout: 6) else {
            miss("no Documents segment on the project screen")
            dumpTree("nav-no-documents-segment")
            return false
        }
        let verifyDocRow = rows().matching(NSPredicate(format: "label CONTAINS[c] %@", "iOS Edit Verify")).firstMatch
        for _ in 0..<3 {
            reliableTap(documentsSegment)
            if verifyDocRow.waitForExistence(timeout: 4) { break }
            sleep(1)
        }
        dumpTree("nav-after-documents-segment")
        guard tapRow("iOS Edit Verify") else {
            miss("seeded verify document not found in General/Documents")
            dumpTree("nav-no-verify-doc-row")
            return false
        }
        sleep(2)
        return true
    }

    /// Taps Save and waits for ANY of the three real outcomes (the sheet
    /// dismissing, the conflict banner appearing, or the "Couldn't save"
    /// alert) — retrying the TAP ITSELF up to 3 times if none appears.
    ///
    /// MEASURED, NOT GUESSED: the first cut of this file tapped Save once
    /// and polled only for the conflict banner. It failed live — the
    /// keyboard was still up (a screenshot at the failure caught the caret
    /// still blinking), and the server's own revision history afterward
    /// showed ZERO new writes, proving the tap never reached the network
    /// layer at all. The likely cause is an ordinary iOS gesture-priority
    /// quirk (a toolbar tap while a text view is first responder can
    /// resign the keyboard instead of activating the button) rather than
    /// anything wrong with `performSave()` itself — but rather than guess
    /// further, this just retries the tap and checks for a real outcome
    /// each time, the same defensive shape `VerificationGaps.pick(_:
    /// inSheet:)` already uses for a different flaky-tap symptom.
    @discardableResult
    private func tapSaveAndWaitForOutcome(timeout: TimeInterval = 15) -> Bool {
        let saveButton = app.navigationBars.buttons["Save"]
        guard saveButton.waitForExistence(timeout: 6) else { miss("no Save button"); return false }
        for attempt in 1...3 {
            reliableTap(saveButton)
            let deadline = Date().addingTimeInterval(timeout)
            while Date() < deadline {
                if !app.navigationBars["Edit document"].exists { return true }
                if staticText(containing: "changed this document while you were editing").exists { return true }
                if app.staticTexts["Couldn't save"].exists { return true }
                usleep(300_000)
            }
            note("Save tap attempt \(attempt) produced no visible outcome within \(Int(timeout))s -- retrying")
        }
        return false
    }

    @discardableResult
    private func openEditor() -> Bool {
        let editButton = app.buttons["Edit document"]
        guard editButton.waitForExistence(timeout: 10) else {
            miss("no 'Edit document' pencil — canWrite resolved false, or the document never finished loading")
            return false
        }
        for _ in 0..<3 {
            reliableTap(editButton)
            if app.navigationBars["Edit document"].waitForExistence(timeout: 5) { return true }
        }
        miss("the editor sheet never opened")
        return false
    }

    // MARK: - direct API calls (host-side test code, real network access —
    // see this file's own header, mechanism 1)

    private func apiRequest(
        _ method: String, _ path: String, base: String = DocumentEditBranches.backendBase,
        token: String? = nil, json: [String: Any]? = nil
    ) -> (status: Int, data: Data)? {
        guard let url = URL(string: base + path) else { return nil }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if let json { req.httpBody = try? JSONSerialization.data(withJSONObject: json) }
        let sem = DispatchSemaphore(value: 0)
        var result: (Int, Data)?
        URLSession.shared.dataTask(with: req) { data, response, _ in
            if let http = response as? HTTPURLResponse, let data {
                result = (http.statusCode, data)
            }
            sem.signal()
        }.resume()
        _ = sem.wait(timeout: .now() + 30)
        return result
    }

    private func apiLogin(email: String, password: String) -> String? {
        guard let (status, data) = apiRequest("POST", "/auth/login", json: [
            "email": email, "password": password, "channel": "mobile",
            "device_id": "docbranches-api-probe", "device_name": "docbranches-api-probe",
            "device_platform": "ios",
        ]), status == 200,
        let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
        let token = obj["token"] as? String else {
            miss("API login failed for \(email)")
            return nil
        }
        return token
    }

    private func apiGetDocument(token: String) -> (title: String, body: String)? {
        guard let (status, data) = apiRequest(
            "GET", "/w/\(Self.workspaceId)/fleet/documents/\(Self.documentId)", token: token
        ), status == 200,
        let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
        let doc = obj["document"] as? [String: Any],
        let title = doc["title"] as? String else { return nil }
        return (title, doc["body"] as? String ?? "")
    }

    @discardableResult
    private func apiPatchDocumentDirect(token: String, title: String, body: String) -> Bool {
        // No base_sha256 -- an UNCONDITIONAL overwrite, deliberately: this
        // simulates a different actor's write landing, and the whole point
        // is that it must succeed regardless of whatever state a previous
        // test run left the document in. fleet_patch_document's own
        // FleetPatchDocumentRequest treats an absent base_sha256 exactly
        // this way (routes_fleet.py: "str(body.base_sha256 or '').strip()
        // or None").
        guard let (status, data) = apiRequest(
            "PATCH", "/w/\(Self.workspaceId)/fleet/documents/\(Self.documentId)",
            token: token, json: ["title": title, "body": body]
        ), status == 200,
        let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
        (obj["ok"] as? Bool) == true else { return false }
        return true
    }

    private func apiRevisionCount(token: String) -> Int? {
        guard let (status, data) = apiRequest(
            "GET", "/w/\(Self.workspaceId)/fleet/documents/\(Self.documentId)/revisions", token: token
        ), status == 200,
        let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
        let revs = obj["revisions"] as? [[String: Any]] else { return nil }
        return revs.count
    }

    /// Mechanism 3 — see this file's own header. `doc-fault-proxy.py` must
    /// already be running on :8098 for this to succeed; a failure here
    /// means the proxy was not started, not that the app misbehaved.
    private func setProxyMode(_ mode: String) {
        guard let (status, _) = apiRequest("GET", "/__mode__/\(mode)", base: Self.proxyControlBase) else {
            miss("could not reach doc-fault-proxy.py on :8098 to set mode=\(mode) — is it running?")
            return
        }
        XCTAssertEqual(status, 200, "the fault-injection proxy refused to set mode=\(mode)")
        note("proxy mode -> \(mode)")
    }

    // MARK: - BRANCH 1: "Use theirs instead" on a 409 conflict

    /// The conflict banner's OTHER action, never driven before. Unlike
    /// "Keep my version" (which saves and dismisses), "Use theirs instead"
    /// discards the local draft, adopts the incoming version INTO the
    /// editor, and leaves the sheet OPEN — `resolveTakeTheirs` never sets
    /// `isPresented = false`. This is the most consequential unexercised
    /// branch in the feature: its own label says "never saved, cannot be
    /// recovered."
    func testUseTheirsInsteadDiscardsMyDraftAndAdoptsIncoming() throws {
        launchFresh()
        signInAs(email: Self.ownerEmail, password: Self.ownerPassword)
        guard navigateToVerifyDocument() else { return }
        shot("00-document-opened")
        guard openEditor() else { return }
        shot("01-editor-opened")

        let bodyEditor = app.textViews.firstMatch
        guard bodyEditor.waitForExistence(timeout: 6) else { miss("no body editor"); return }
        let myMarker = "MY-LOCAL-EDIT-\(Int(Date().timeIntervalSince1970))"
        bodyEditor.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.9)).tap()
        usleep(500_000)
        bodyEditor.typeText("\n\n\(myMarker) — must be discarded by Use theirs instead.")
        shot("02-my-edit-typed")

        // The sheet has captured baseSha256 = S1 the moment it opened. Now
        // advance the server PAST it directly, exactly as a different actor
        // editing the same document concurrently would (mechanism 1 — see
        // this file's own header).
        guard let ownerToken = apiLogin(email: Self.ownerEmail, password: Self.ownerPassword) else {
            miss("could not obtain an API token to make the conflicting edit"); return
        }
        guard let current = apiGetDocument(token: ownerToken) else {
            miss("could not read the document to build the conflicting edit"); return
        }
        let theirMarker = "HOST-EDIT-\(Int(Date().timeIntervalSince1970))"
        let theirBody = current.body + "\n\n\(theirMarker) — this is what Use theirs instead should adopt."
        guard apiPatchDocumentDirect(token: ownerToken, title: current.title, body: theirBody) else {
            miss("the host-side conflicting PATCH did not succeed"); return
        }
        note("host-side conflicting edit landed, marker=\(theirMarker)")
        let revisionsBeforeChoice = apiRevisionCount(token: ownerToken)

        guard tapSaveAndWaitForOutcome() else {
            miss("Save produced no outcome at all after 3 attempts")
            dumpTree("save-no-outcome")
            return
        }
        let headline = staticText(containing: "changed this document while you were editing")
        guard headline.waitForExistence(timeout: 5) else {
            miss("Save produced an outcome, but it was not the 409 conflict banner")
            dumpTree("no-conflict-rendered")
            return
        }
        shot("03-conflict-banner")
        XCTAssertTrue(button(containing: "Use theirs instead").exists, "no 'Use theirs instead' action on the conflict banner")

        reliableTap(button(containing: "Use theirs instead"))
        sleep(2)
        shot("04-after-use-theirs")

        // The sheet must STAY OPEN — unlike "Keep my version", which saves
        // and dismisses, resolveTakeTheirs never sets isPresented = false.
        XCTAssertTrue(app.navigationBars["Edit document"].exists,
                      "the editor closed after 'Use theirs instead' — it must stay open, now showing the adopted version")

        let bodyNow = (app.textViews.firstMatch.value as? String) ?? ""
        note("body after Use-theirs (tail) = \(bodyNow.suffix(220))")
        XCTAssertFalse(bodyNow.contains(myMarker),
                       "the discarded draft is STILL present in the editor after 'Use theirs instead' — it must be gone")
        XCTAssertTrue(bodyNow.contains(theirMarker),
                      "the incoming version was not adopted into the editor after 'Use theirs instead'")

        // Nothing was WRITTEN to the server by this action — the revision
        // count must be unchanged from right after the host's own edit.
        let revisionsAfterChoice = apiRevisionCount(token: ownerToken)
        note("revisions before-choice=\(revisionsBeforeChoice ?? -1) after-use-theirs=\(revisionsAfterChoice ?? -1)")
        if let before = revisionsBeforeChoice, let after = revisionsAfterChoice {
            XCTAssertEqual(before, after, "'Use theirs instead' wrote a NEW revision — it must discard silently, never save")
        } else {
            miss("could not read revision counts to confirm 'Use theirs instead' wrote nothing")
        }

        // Nothing is left dirty, so Cancel must close with NO discard
        // confirmation. Retried the same way tapSaveAndWaitForOutcome
        // retries Save -- a single un-retried tap on this exact toolbar
        // shape has already shown it can be swallowed once (see that
        // helper's own header note).
        let cancelButton = app.navigationBars.buttons["Cancel"]
        guard cancelButton.waitForExistence(timeout: 5) else { miss("no Cancel button"); return }
        for _ in 0..<3 {
            reliableTap(cancelButton)
            if waitUntilGone(app.navigationBars["Edit document"], timeout: 5) { break }
        }
        sleep(1)
        shot("05-closed")
        XCTAssertFalse(app.navigationBars["Edit document"].exists, "Cancel after adopting the incoming version did not close the sheet")
        XCTAssertFalse(app.staticTexts["Discard changes?"].exists,
                       "a discard confirmation appeared even though nothing was left unsaved")

        // What is rendered in the document detail view now must be the
        // ADOPTED (theirs) text, not what was locally typed.
        XCTAssertTrue(staticText(containing: theirMarker).waitForExistence(timeout: 8),
                      "the document view does not show the adopted (theirs) text after closing")
        print("USE_THEIRS_DONE misses=\(misses.count) \(misses)")
    }

    // MARK: - BRANCH 2: background + restore, and the reverse

    /// Type a draft, background the app (flushing to disk), TERMINATE the
    /// suspended process (the real "the OS reclaimed it while backgrounded"
    /// case a bare background+foreground never actually tests, since a
    /// merely-suspended app keeps its in-memory `@State` regardless of this
    /// feature), relaunch fresh, reopen the SAME document's editor, and
    /// confirm the draft comes back. Then confirm the reverse: a SAVED
    /// draft must never resurrect as a stale "restored draft" notice after
    /// the identical background+terminate+relaunch cycle.
    func testBackgroundAndRestoreDraft() throws {
        launchFresh()
        signInAs(email: Self.ownerEmail, password: Self.ownerPassword)
        guard navigateToVerifyDocument() else { return }
        guard openEditor() else { return }
        shot("01-editor-opened")

        let bodyEditor = app.textViews.firstMatch
        guard bodyEditor.waitForExistence(timeout: 6) else { miss("no body editor"); return }
        let draftMarker = "BACKGROUND-DRAFT-\(Int(Date().timeIntervalSince1970))"
        bodyEditor.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.9)).tap()
        usleep(500_000)
        bodyEditor.typeText("\n\n\(draftMarker) — never saved, must survive a background + kill.")
        shot("02-draft-typed")

        // Give the ~600ms debounce time to flush before backgrounding, so
        // the scenePhase flush (belt-and-braces) is not the only thing
        // saving this draft to disk.
        sleep(1)

        backgroundTerminateRelaunch()
        guard app.tabBars.firstMatch.waitForExistence(timeout: 20) else {
            miss("session did not restore after the first relaunch"); return
        }
        sleep(2)
        shot("03-relaunched-signed-in")

        guard navigateToVerifyDocument() else { return }
        guard openEditor() else { return }
        shot("04-editor-reopened")

        let restoredNotice = staticText(containing: "Restored your unsaved draft")
        XCTAssertTrue(restoredNotice.waitForExistence(timeout: 8),
                      "no 'Restored your unsaved draft' notice after a background + relaunch")

        let restoredText = (app.textViews.firstMatch.value as? String) ?? ""
        note("restored body (tail) = \(restoredText.suffix(220))")
        XCTAssertTrue(restoredText.contains(draftMarker),
                      "the restored draft does not contain what was typed before backgrounding")

        // ---- THE REVERSE: a SAVED draft must not resurrect as stale -----
        guard tapSaveAndWaitForOutcome() else { miss("Save produced no outcome after 3 attempts"); return }
        sleep(1)
        shot("05-saved")
        XCTAssertFalse(app.navigationBars["Edit document"].exists, "saving the restored draft did not dismiss the editor")

        backgroundTerminateRelaunch()
        guard app.tabBars.firstMatch.waitForExistence(timeout: 20) else {
            miss("session did not restore after the second relaunch"); return
        }
        sleep(2)

        guard navigateToVerifyDocument() else { return }
        guard openEditor() else { return }
        shot("06-editor-after-save-and-relaunch")

        XCTAssertFalse(staticText(containing: "Restored your unsaved draft").exists,
                       "a SAVED draft resurrected as a stale 'restored draft' notice")
        print("BG_RESTORE_DONE misses=\(misses.count) \(misses)")
    }

    /// `.home` backgrounds without terminating; a following `terminate()`
    /// is what actually reclaims the process — a plain background+
    /// foreground alone would prove nothing this feature's in-memory
    /// `@State` did not already guarantee. `README.md`'s own note that
    /// `xcrun simctl` "can send the app to background" describes the same
    /// mechanism `XCUIDevice.shared.press(.home)` reaches from inside the
    /// test.
    private func backgroundTerminateRelaunch() {
        XCUIDevice.shared.press(.home)
        sleep(2)
        app.terminate()
        sleep(1)
        // A stored refresh token survives a plain terminate (Keychain is
        // untouched by it), so this should land straight back on the
        // signed-in tab bar with no credentials re-entered.
        app.launch()
        sleep(2)
    }

    // MARK: - BRANCH 3: viewer-role gating

    /// A real second account, workspace role `viewer` — created through the
    /// real signup + invite + accept HTTP flow (see this file's own commit
    /// message), never by editing the database. `DocumentAuthoring.
    /// canWrite` is a WORKSPACE-role check, coarser than the web's
    /// per-project ACL by its own design (see that file's header) — a
    /// viewer fails it regardless of project membership, so the pencil must
    /// be ABSENT, never merely disabled (this codebase's "no dead controls"
    /// law), and the document must still READ correctly.
    func testViewerCannotEditButCanRead() throws {
        // Seed a KNOWN body first so the read assertion does not depend on
        // what order the other tests in this file happened to run in.
        guard let ownerToken = apiLogin(email: Self.ownerEmail, password: Self.ownerPassword) else {
            miss("could not log in as owner to seed a known document body"); return
        }
        let readMarker = "VIEWER-READ-CHECK-\(Int(Date().timeIntervalSince1970))"
        guard apiPatchDocumentDirect(
            token: ownerToken, title: "iOS Edit Verify",
            body: "Seeded for the viewer-gating check.\n\n\(readMarker)"
        ) else {
            miss("could not seed a known document body via the API"); return
        }

        launchFresh()
        signInAs(email: Self.viewerEmail, password: Self.viewerPassword)
        dumpTree("viewer-after-signin")

        guard navigateToVerifyDocument() else { miss("the viewer could not reach the seeded document at all"); return }
        shot("01-viewer-document-open")

        // ABSENT, not disabled.
        let editButton = app.buttons["Edit document"]
        XCTAssertFalse(editButton.exists,
                       "a viewer sees the edit pencil — it must not be rendered at all, per this codebase's 'no dead controls' law")

        // The document still READS correctly.
        XCTAssertTrue(staticText(containing: "iOS Edit Verify").waitForExistence(timeout: 8),
                      "the viewer cannot see the document title")
        XCTAssertTrue(staticText(containing: readMarker).waitForExistence(timeout: 8),
                      "the viewer cannot see the document body — a read-only account should still be able to READ")
        print("VIEWER_GATE_DONE misses=\(misses.count) \(misses)")
    }

    // MARK: - BRANCH 4: .unauthorized and .decoding, live over the wire

    /// Point the app at the fault-injection proxy (mechanism 3 — see this
    /// file's own header) for one launch, sign in fresh (forwarded through
    /// unchanged, so this really talks to the real backend), reach the
    /// editor, and drive both outcomes in one pass:
    ///
    ///   1. force a 401 on the specific PATCH (`.unauthorized`) — confirm
    ///      NO "Couldn't save" alert, the draft surviving untouched, and
    ///      that retrying the SAME tap once the fault clears actually
    ///      saves (proving the sheet is genuinely still usable, not merely
    ///      visually present).
    ///   2. force a 2xx with an undecodable body (`.decoding`) — confirm it
    ///      is treated as a quiet success, matching the code's own
    ///      documented posture ("the write LANDED, only the confirmation
    ///      was unreadable... treated as success").
    func testUnauthorizedAndDecodingOnSave() throws {
        setProxyMode("normal")
        launchFresh(extraEnv: ["EMPYRALIS_API_BASE_URL": Self.proxyAPIBase])
        signInAs(email: Self.ownerEmail, password: Self.ownerPassword)
        guard navigateToVerifyDocument() else { return }
        guard openEditor() else { return }
        shot("01-editor-opened")

        // ---- .unauthorized ---------------------------------------------
        let bodyEditor = app.textViews.firstMatch
        guard bodyEditor.waitForExistence(timeout: 6) else { miss("no body editor"); return }
        let unauthMarker = "UNAUTH-DRAFT-\(Int(Date().timeIntervalSince1970))"
        bodyEditor.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.9)).tap()
        usleep(500_000)
        bodyEditor.typeText("\n\n\(unauthMarker) — must survive a 401 on Save.")
        shot("02-unauthorized-typed")

        setProxyMode("force_401")
        let saveButton = app.navigationBars.buttons["Save"]
        guard saveButton.waitForExistence(timeout: 6) else { miss("no Save button"); return }
        // Two taps, deliberately NOT the shared tapSaveAndWaitForOutcome
        // helper: a swallowed first tap (keyboard resign competing with the
        // toolbar button) and a genuinely-handled 401 look IDENTICAL on
        // screen -- nothing visibly changes either way, which is the whole
        // point of .unauthorized being silent. Both taps are safe here:
        // force_401 refuses every attempt identically, and doc-fault-
        // proxy.py's own log (captured separately) is the definitive record
        // of how many requests actually arrived.
        reliableTap(saveButton)
        sleep(2)
        reliableTap(saveButton)
        sleep(3)
        shot("03-after-401-save")

        XCTAssertFalse(app.staticTexts["Couldn't save"].exists,
                       "a 401 rendered the generic 'Couldn't save' alert — .unauthorized must be handled silently (refresh-and-retry, or a full sign-out), never shown as an ordinary failure")
        let stillOpen = app.navigationBars["Edit document"].exists
        note("after 401: sheet still open=\(stillOpen)")
        XCTAssertTrue(stillOpen,
                      "the editor closed on a 401 — the silent refresh must have failed, signing the whole session out; that IS a legitimate branch of handleUnauthorized(), but it means the refresh token was not actually valid here")
        let bodyAfter401 = (app.textViews.firstMatch.value as? String) ?? ""
        XCTAssertTrue(bodyAfter401.contains(unauthMarker),
                      "the draft was lost after a 401 — performSave's .unauthorized branch must leave it exactly as typed")

        // Clear the fault and retry the SAME tap — proves the sheet is
        // genuinely still usable after a silent refresh, not just visually
        // present.
        setProxyMode("normal")
        guard tapSaveAndWaitForOutcome() else { miss("Save produced no outcome after 3 attempts, post-401"); return }
        sleep(1)
        shot("04-retry-after-401-cleared")
        XCTAssertFalse(app.navigationBars["Edit document"].exists, "retrying Save after the fault cleared did not succeed — the sheet was left unusable by the 401")
        XCTAssertTrue(staticText(containing: unauthMarker).waitForExistence(timeout: 8), "the retried save did not land")

        // ---- .decoding ---------------------------------------------------
        guard openEditor() else { miss("could not reopen the editor for the .decoding case"); return }

        let bodyEditor2 = app.textViews.firstMatch
        guard bodyEditor2.waitForExistence(timeout: 6) else { miss("no body editor"); return }
        let decodingMarker = "DECODING-QUIET-SUCCESS-\(Int(Date().timeIntervalSince1970))"
        bodyEditor2.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.9)).tap()
        usleep(500_000)
        bodyEditor2.typeText("\n\n\(decodingMarker) — a 2xx with an undecodable body must be a quiet success.")
        shot("05-decoding-typed")

        setProxyMode("force_decoding")
        guard tapSaveAndWaitForOutcome() else { miss("Save produced no outcome after 3 attempts, decoding case"); return }
        sleep(1)
        shot("06-after-decoding-save")
        XCTAssertFalse(app.navigationBars["Edit document"].exists,
                       "a 2xx with an undecodable body did NOT dismiss the editor — .decoding must be treated as success, per DocumentEditSheet's own documented posture")
        XCTAssertFalse(app.staticTexts["Couldn't save"].exists, "an undecodable-but-2xx response showed a failure alert")
        XCTAssertTrue(staticText(containing: decodingMarker).waitForExistence(timeout: 8),
                      "the quietly-successful edit is not rendered back in the document view")

        setProxyMode("normal")
        print("UNAUTH_DECODING_DONE misses=\(misses.count) \(misses)")
    }
}

import SwiftUI
import UIKit

/// Edit a document's title and body together, as one explicit Save — full
/// screen sheet, Cancel/Save toolbar, the same chrome family
/// TaskDetailView's own `TextEditSheet` already uses for its title/
/// description edits.
///
/// ── AUTOSAVE VS EXPLICIT SAVE, AND WHY THIS PICKS EXPLICIT ─────────────
/// The web autosaves on a ~900ms debounce and flushes on blur — correct
/// there, because a browser tab is a long-lived, always-connected session
/// and the debounce exists only to avoid a PATCH per keystroke. None of
/// that transfers cleanly to a phone:
///
///   • the app can be BACKGROUNDED mid-edit at any moment, with no reliable
///     way to guarantee a pending debounced save actually fires before the
///     OS suspends the process — a timer that "usually" runs is not a
///     contract, and this codebase's own standing rule is that text a
///     person typed must never be lost silently;
///   • network is genuinely less reliable off a desk (cellular handoffs,
///     dead zones), and firing a real PATCH on every pause in typing turns
///     ordinary flakiness into a stream of spurious 409s — every failed
///     autosave attempt against a document an agent is also touching is one
///     more chance to interrupt the person with a conflict banner mid-
///     thought, for no reason they asked for;
///   • this app ALREADY has a house pattern for exactly this shape of edit
///     — TaskDetailView's `TextEditSheet` (title/description) is explicit
///     Cancel/Save, not live autosave, so explicit Save is the established
///     convention here, not a deviation invented for documents.
///
/// So: nothing is sent to the server until Save is tapped. But "never lost
/// silently" still has to be true off that one deliberate choice, and the
/// answer is a SEPARATE, cheaper safety net — see DocumentDraftStore.swift.
/// This sheet writes a local-disk snapshot of the draft (debounced ~600ms,
/// pure disk I/O, no network, so it can never itself produce a conflict) and
/// flushes it immediately when the scene backgrounds. Reopening the editor
/// for the same document restores that draft — carrying forward the exact
/// `base_sha256` it was composed against, so a restored draft that has gone
/// stale while the app was away still correctly hits the 409 conflict path
/// on Save rather than silently overwriting an intervening edit. This is
/// belt-and-braces beyond what the task strictly asked for, and is judged
/// worth it because it is the one gap explicit-Save alone does not close:
/// a hard termination while backgrounded loses in-memory `@State` outright.
///
/// ── ESCAPE / BACK MUST NOT DISCARD WITHOUT A CHOICE ─────────────────────
/// Swipe-to-dismiss is disabled outright while there is a real, unsaved
/// difference from the last confirmed server state (`interactiveDismiss
/// Disabled(isDirty)`) — the person cannot accidentally flick this away.
/// The explicit Cancel button is the only way out while dirty, and it asks
/// first (`showDiscardConfirm`) rather than discarding on the first tap.
///
/// ── THE CONFLICT PATH IS THE REASON THIS FEATURE IS DELICATE ────────────
/// See DocumentConflict.swift's own header for the full reasoning; this
/// view is the one place its `Plan` actually renders. On a 409 the draft is
/// left exactly as typed, the toolbar Save button is replaced by the two
/// explicit resolution actions (never both fitting on screen as separate
/// controls — the SAVE button's own slot is where the choice belongs, since
/// saving is literally what is blocked), and nothing is written until the
/// person picks one.
struct DocumentEditSheet: View {
    /// The full, body-carrying document as last confirmed with the server —
    /// title, body AND `stateSha256` (this edit's starting precondition
    /// token). Never the list-row shape, which has no body — DocumentDetailView
    /// only offers the edit affordance once it holds this shape.
    let document: EmpDocument
    @Binding var isPresented: Bool
    /// Fired the moment this sheet has a document state the caller's own
    /// cache does not yet have and KNOWS to be current — that is usually a
    /// CONFIRMED WRITE (a real 2xx with a decodable body, a 2xx whose body
    /// could not be decoded but which we know landed, or an identical-
    /// content "conflict" silently adopted), and exactly once for those.
    /// The one exception is `resolveTakeTheirs` — "Use theirs instead"
    /// writes nothing, but the incoming document it adopts is itself a
    /// just-fetched read of server truth (it arrived on the 409), so
    /// relaying it here is what keeps the caller from showing a body the
    /// person has already looked at and moved past as if it were still
    /// current. Never fired on a real conflict AWAITING a decision, never
    /// on failure.
    let onSaved: (EmpDocument) -> Void

    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var store: WorkspaceStore
    @Environment(\.colorScheme) private var scheme
    @Environment(\.scenePhase) private var scenePhase

    @State private var draftTitle: String
    @State private var draftBody: String
    @State private var lastSavedTitle: String
    @State private var lastSavedBody: String
    @State private var baseSha256: String?
    @State private var restoredDraftNotice: Bool

    @State private var status: SaveStatus = .idle
    @State private var conflictDocument: EmpDocument?
    @State private var showConflictDiff = false
    @State private var showDiscardConfirm = false
    @State private var draftPersistTask: Task<Void, Never>?

    @FocusState private var titleFocused: Bool
    @FocusState private var bodyFocused: Bool

    private enum SaveStatus: Equatable {
        case idle
        case saving
        case error(String)
    }

    init(document: EmpDocument, isPresented: Binding<Bool>, onSaved: @escaping (EmpDocument) -> Void) {
        self.document = document
        self._isPresented = isPresented
        self.onSaved = onSaved

        let committedTitle = document.title
        let committedBody = document.body ?? ""

        if let restored = DocumentDraftStore.load(for: document.id),
           DocumentAuthoring.canSave(
               draftTitle: restored.title, draftBody: restored.body,
               lastSavedTitle: committedTitle, lastSavedBody: committedBody
           ) {
            self._draftTitle = State(initialValue: restored.title)
            self._draftBody = State(initialValue: restored.body)
            self._baseSha256 = State(initialValue: restored.baseSha256)
            self._restoredDraftNotice = State(initialValue: true)
        } else {
            // No draft, or a leftover one that turned out identical to what
            // is already saved — nothing to restore, and nothing worth
            // keeping around either.
            DocumentDraftStore.clear(for: document.id)
            self._draftTitle = State(initialValue: committedTitle)
            self._draftBody = State(initialValue: committedBody)
            self._baseSha256 = State(initialValue: document.stateSha256)
            self._restoredDraftNotice = State(initialValue: false)
        }
        self._lastSavedTitle = State(initialValue: committedTitle)
        self._lastSavedBody = State(initialValue: committedBody)
    }

    private var isDirty: Bool {
        DocumentAuthoring.canSave(
            draftTitle: draftTitle, draftBody: draftBody,
            lastSavedTitle: lastSavedTitle, lastSavedBody: lastSavedBody
        )
    }

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.bgPage(scheme).ignoresSafeArea()
                if let conflictDocument {
                    conflictBanner(conflictDocument)
                } else {
                    editorForm
                }
            }
            .navigationTitle("Edit document")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel", action: requestCancel)
                        .font(.empBody)
                        .foregroundStyle(Theme.textSecondary(scheme))
                        .disabled(status == .saving)
                }
                // While a conflict is showing, the Save slot is replaced by
                // the banner's own two actions — offering a THIRD way to
                // attempt a write here would defeat the point of blocking it.
                if conflictDocument == nil {
                    ToolbarItem(placement: .topBarTrailing) {
                        saveButton
                    }
                }
            }
        }
        .interactiveDismissDisabled(isDirty)
        // `.constant`, matching TaskDetailView's identical alert exactly —
        // an alert has no swipe-to-dismiss to reconcile against, so the
        // button's own action is the one and only place presentation ends.
        .alert("Couldn't save", isPresented: .constant(errorMessage != nil)) {
            Button("OK") { status = .idle }
        } message: {
            Text(errorMessage ?? "")
        }
        .confirmationDialog("Discard changes?", isPresented: $showDiscardConfirm, titleVisibility: .visible) {
            Button("Discard changes", role: .destructive, action: dismissWithoutSaving)
            Button("Keep editing", role: .cancel) {}
        } message: {
            Text("What you typed here has not been saved and cannot be recovered.")
        }
        .onChange(of: draftTitle) { _, _ in scheduleDraftPersist() }
        .onChange(of: draftBody) { _, _ in scheduleDraftPersist() }
        .onChange(of: scenePhase) { _, newPhase in
            if newPhase == .background {
                draftPersistTask?.cancel()
                persistDraftNow()
            }
        }
    }

    private var saveButton: some View {
        Button(action: performSave) {
            if status == .saving {
                ProgressView()
                    .tint(Theme.textPrimary(scheme))
            } else {
                Text("Save")
            }
        }
        .font(.empBodyMedium)
        .foregroundStyle(isDirty && status != .saving ? Theme.textPrimary(scheme) : Theme.textMuted(scheme))
        .disabled(!isDirty || status == .saving)
    }

    private var errorMessage: String? {
        if case .error(let message) = status { return message }
        return nil
    }

    // MARK: - Editor

    private var editorForm: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.x4) {
                if restoredDraftNotice {
                    restoredNotice
                }
                titleField
                bodyField
            }
            .padding(Space.x4)
        }
        .scrollDismissesKeyboard(.interactively)
    }

    private var restoredNotice: some View {
        HStack(spacing: Space.x2) {
            Image(systemName: "arrow.uturn.backward")
                .font(.system(size: 11, weight: .medium))
            Text("Restored your unsaved draft from earlier.")
                .font(.empCaption)
        }
        .foregroundStyle(Theme.textMuted(scheme))
        .padding(.horizontal, Space.x3)
        .padding(.vertical, Space.x2)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.bgInset(scheme), in: RoundedRectangle(cornerRadius: Radius.control))
    }

    /// No focus ring — the border stepping from `border` to `borderStrong`
    /// plus the caret is the whole signal, matching every text field in
    /// this app (CLAUDE.md: "THERE IS NO FOCUS RING", founder's decision).
    private var titleField: some View {
        TextField("Document title", text: $draftTitle, axis: .vertical)
            .font(.empBodyMedium)
            .foregroundStyle(Theme.textPrimary(scheme))
            .lineLimit(1...4)
            .focused($titleFocused)
            .padding(Space.x3)
            .background(Theme.bgField(scheme), in: RoundedRectangle(cornerRadius: Radius.control))
            .overlay(
                RoundedRectangle(cornerRadius: Radius.control)
                    .stroke(titleFocused ? Theme.borderStrong(scheme) : Theme.border(scheme), lineWidth: 1)
            )
    }

    private var bodyField: some View {
        ZStack(alignment: .topLeading) {
            if draftBody.isEmpty {
                Text("Write in Markdown…")
                    .font(.empBody)
                    .foregroundStyle(Theme.textMuted(scheme))
                    .padding(.horizontal, Space.x3)
                    .padding(.vertical, Space.x3 + 4)
                    .allowsHitTesting(false)
            }
            TextEditor(text: $draftBody)
                .font(.empBody)
                .foregroundStyle(Theme.textPrimary(scheme))
                .scrollContentBackground(.hidden)
                .focused($bodyFocused)
                .frame(minHeight: 320)
                .padding(Space.x2)
        }
        .background(Theme.bgField(scheme), in: RoundedRectangle(cornerRadius: Radius.control))
        .overlay(
            RoundedRectangle(cornerRadius: Radius.control)
                .stroke(bodyFocused ? Theme.borderStrong(scheme) : Theme.border(scheme), lineWidth: 1)
        )
    }

    // MARK: - Conflict banner

    /// The whole reason this feature is delicate — see this file's own
    /// header and DocumentConflict.swift's. `plan` is recomputed from
    /// current live state on every render rather than cached, so the
    /// headline/actions always describe exactly what is on screen right now.
    private func conflictBanner(_ current: EmpDocument) -> some View {
        let mine = DocumentConflict.Side(title: DocumentAuthoring.resolvedTitle(draftTitle), body: draftBody)
        let theirs = DocumentConflict.Side(title: current.title, body: current.body ?? "")
        let plan = DocumentConflict.plan(mine: mine, theirs: theirs, actorName: conflictActorName(current))

        return ScrollView {
            VStack(alignment: .leading, spacing: Space.x5) {
                VStack(alignment: .leading, spacing: Space.x2) {
                    HStack(spacing: Space.x2) {
                        Image(systemName: "exclamationmark.triangle")
                            .font(.system(size: 14, weight: .medium))
                            .foregroundStyle(Theme.warning(scheme))
                        Text("Paused — this document changed elsewhere")
                            .font(.empCaptionMedium)
                            .foregroundStyle(Theme.warning(scheme))
                    }
                    Text(plan.headline)
                        .font(.empBodyMedium)
                        .foregroundStyle(Theme.textPrimary(scheme))
                        .fixedSize(horizontal: false, vertical: true)
                    Text(plan.reassurance)
                        .font(.empSecondary)
                        .foregroundStyle(Theme.textSecondary(scheme))
                        .fixedSize(horizontal: false, vertical: true)
                }

                VStack(spacing: Space.x2) {
                    ForEach(plan.actions) { action in
                        conflictActionButton(action)
                    }
                }

                diffDisclosure(mine: mine.body, theirs: theirs.body)
            }
            .padding(Space.x4)
        }
    }

    /// Weight and fill, never a second hue — "Keep my version" is the ONE
    /// accent-filled control this whole sheet ever draws, and only while a
    /// conflict is actually showing.
    private func conflictActionButton(_ action: DocumentConflict.Action) -> some View {
        Button {
            switch action.key {
            case .keepMine: resolveKeepMine()
            case .takeTheirs: resolveTakeTheirs()
            }
        } label: {
            VStack(alignment: .leading, spacing: 2) {
                Text(action.label)
                    .font(.empBodyMedium)
                    .foregroundStyle(action.accent ? Theme.accentContrast : Theme.textPrimary(scheme))
                Text(action.consequence)
                    .font(.empCaption)
                    .foregroundStyle(action.accent ? Theme.accentContrast.opacity(0.85) : Theme.textMuted(scheme))
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(Space.x3)
            .background(
                action.accent ? Theme.accent(scheme) : Theme.bgCard(scheme),
                in: RoundedRectangle(cornerRadius: Radius.control)
            )
            .overlay(
                RoundedRectangle(cornerRadius: Radius.control)
                    .stroke(action.accent ? Color.clear : Theme.borderStrong(scheme), lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder
    private func diffDisclosure(mine: String, theirs: String) -> some View {
        Button {
            showConflictDiff.toggle()
        } label: {
            HStack(spacing: Space.x1) {
                Text(showConflictDiff ? "Hide what changed" : "See what changed")
                Image(systemName: showConflictDiff ? "chevron.up" : "chevron.down")
                    .font(.system(size: 10, weight: .semibold))
            }
            .font(.empCaption)
            .foregroundStyle(Theme.textSecondary(scheme))
        }
        .buttonStyle(.plain)

        if showConflictDiff {
            let diff = DocumentConflict.diffBodies(mine, theirs)
            VStack(alignment: .leading, spacing: 2) {
                ForEach(Array(diff.lines.enumerated()), id: \.offset) { _, line in
                    diffLineView(line)
                }
                if diff.truncated {
                    Text("Showing the first \(diff.lines.count) changed lines.")
                        .font(.empCaption)
                        .foregroundStyle(Theme.textMuted(scheme))
                        .padding(.top, Space.x1)
                }
            }
            .padding(Space.x3)
            .background(Theme.bgInset(scheme), in: RoundedRectangle(cornerRadius: Radius.card))
        }
    }

    private func diffLineView(_ line: DocumentConflict.DiffLine) -> some View {
        HStack(alignment: .top, spacing: Space.x2) {
            Text(diffPrefix(line.kind))
                .font(.empMono)
                .foregroundStyle(diffColor(line.kind))
                .frame(width: 12, alignment: .leading)
            Text(line.text.isEmpty ? " " : line.text)
                .font(.empMono)
                .foregroundStyle(diffColor(line.kind))
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func diffPrefix(_ kind: DocumentConflict.DiffLineKind) -> String {
        switch kind {
        case .context: return " "
        case .add: return "+"
        case .remove: return "-"
        }
    }

    private func diffColor(_ kind: DocumentConflict.DiffLineKind) -> Color {
        switch kind {
        case .context: return Theme.textMuted(scheme)
        case .add: return Theme.online(scheme)
        case .remove: return Theme.offline(scheme)
        }
    }

    /// Who changed it, in the words a person uses — resolved off the SAME
    /// agents/members lists every other actor row in this app resolves
    /// against, never a guess. Only a genuinely RESOLVED identity is passed
    /// through; `.unknown`/`.lookupFailed` both become `nil`, which
    /// `DocumentConflict.actorLabel` turns into "Someone else" — a failed
    /// lookup must never read as a resolved-but-oddly-named actor (e.g.
    /// "Couldn't load who changed this document").
    private func conflictActorName(_ current: EmpDocument) -> String? {
        guard let resolved = ActorResolver.resolve(
            id: current.updatedBy,
            agents: store.agents,
            members: store.members,
            identityLookupFailed: store.identityLookupFailed
        ) else { return nil }
        switch resolved {
        case .person, .agent, .external:
            return resolved.name
        case .unknown, .lookupFailed:
            return nil
        }
    }

    // MARK: - Save

    private func performSave() {
        guard status != .saving else { return }
        guard let workspaceId = session.currentWorkspaceId else {
            status = .error("No workspace.")
            return
        }
        status = .saving
        let title = DocumentAuthoring.resolvedTitle(draftTitle)
        let bodyToSend = draftBody
        let sha = baseSha256
        Task {
            do {
                let saved = try await DocumentWriteAPI.patchDocument(
                    workspaceId: workspaceId,
                    documentId: document.id,
                    title: title,
                    body: bodyToSend,
                    baseSha256: sha
                )
                applySuccess(saved: saved, sentTitle: title, sentBody: bodyToSend)
            } catch DocumentWriteError.unauthorized {
                // A 401 means the server rejected this attempt at the auth
                // layer BEFORE the route body ran, so nothing was written —
                // safe to just reset and let the person retry. handleUnauthorized
                // may transparently refresh the session (this screen needs
                // no special handling for that) or sign out entirely (in
                // which case this sheet is about to be torn down with the
                // rest of the authenticated view hierarchy regardless).
                // Matches WorkspaceStore.commit()'s own established posture
                // for the identical case.
                await session.handleUnauthorized()
                status = .idle
            } catch DocumentWriteError.conflict(let current, _) {
                handleConflict(current: current, mine: .init(title: title, body: bodyToSend))
            } catch DocumentWriteError.server(let message) {
                status = .error(message)
            } catch DocumentWriteError.decoding {
                // 2xx: the write landed, only the reply was unreadable.
                // Treated as success, matching WorkspaceStore.commit()'s
                // own established posture for tasks — "rolling back here
                // would be the lie". baseSha256 is deliberately left
                // untouched; see applyQuietSuccess.
                applyQuietSuccess(sentTitle: title, sentBody: bodyToSend)
            } catch {
                status = .error("Couldn't confirm whether that saved. Check your connection and try again.")
            }
        }
    }

    private func applySuccess(saved: EmpDocument, sentTitle: String, sentBody: String) {
        lastSavedTitle = sentTitle
        lastSavedBody = sentBody
        baseSha256 = saved.stateSha256
        status = .idle
        DocumentDraftStore.clear(for: document.id)
        draftPersistTask?.cancel()
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
        onSaved(saved)
        isPresented = false
    }

    /// The write landed but its confirmation could not be read, so there is
    /// no fresh `state_sha256` to adopt. `baseSha256` is left exactly as it
    /// was — if the person edits again and saves, that PATCH will compare
    /// against a base the server has already moved past (by this very
    /// write) and correctly 409. At that point `DocumentConflict.plan`
    /// compares the new draft against what THIS write just landed: if the
    /// person has not touched the title/body since, the two are identical
    /// and the conflict resolves silently, adopting the real sha with
    /// nothing lost. A stranger stepping on the same document in that same
    /// window would show as a genuine conflict, correctly. This is the
    /// intentional self-healing path the header above describes — no
    /// forced refetch, no extra round trip on the common case.
    private func applyQuietSuccess(sentTitle: String, sentBody: String) {
        lastSavedTitle = sentTitle
        lastSavedBody = sentBody
        status = .idle
        DocumentDraftStore.clear(for: document.id)
        draftPersistTask?.cancel()
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
        onSaved(syntheticDocument(title: sentTitle, body: sentBody))
        isPresented = false
    }

    /// A best-effort document reflecting what was just SENT, for the caller
    /// to render immediately — used only when the real server confirmation
    /// could not be read. `stateSha256` here is the OLD token (`baseSha256`
    /// is untouched in this branch), which is honest: we do not know the
    /// real one.
    private func syntheticDocument(title: String, body: String) -> EmpDocument {
        EmpDocument(
            id: document.id,
            title: title,
            path: document.path,
            projectId: document.projectId,
            body: body,
            createdBy: document.createdBy,
            updatedBy: document.updatedBy,
            createdAt: document.createdAt,
            updatedAt: document.updatedAt,
            stateSha256: baseSha256
        )
    }

    private func handleConflict(current: EmpDocument, mine: DocumentConflict.Side) {
        let plan = DocumentConflict.plan(
            mine: mine,
            theirs: .init(title: current.title, body: current.body ?? ""),
            actorName: conflictActorName(current)
        )
        // Adopt the new base regardless of which branch follows — the old
        // one is definitely stale now, and the identical-content branch
        // below needs it too.
        baseSha256 = current.stateSha256

        if !plan.needsResolution {
            // Byte-identical to what the server already holds — nothing was
            // actually lost, so there is nothing to interrupt the person
            // about. Adopt silently and treat as saved, matching the web.
            lastSavedTitle = mine.title
            lastSavedBody = mine.body
            status = .idle
            DocumentDraftStore.clear(for: document.id)
            draftPersistTask?.cancel()
            onSaved(syntheticDocument(title: mine.title, body: mine.body))
            isPresented = false
            return
        }

        status = .idle
        conflictDocument = current
    }

    private func resolveKeepMine() {
        // Rebase onto the state the refusal handed back and save the
        // person's version. The other version is not destroyed — it is
        // already a revision, visible in this document's own history.
        // `baseSha256` was already updated to the conflict's own sha in
        // `handleConflict`, so this retry is a genuine rebase, not a repeat
        // of the same doomed request.
        conflictDocument = nil
        showConflictDiff = false
        performSave()
    }

    private func resolveTakeTheirs() {
        // Replaces the draft with the incoming version. This DOES discard
        // what the person typed — it was never saved, so no revision holds
        // it — which is exactly why the button says so and why nothing
        // reaches this path without an explicit tap.
        guard let incoming = conflictDocument else { return }
        let title = incoming.title
        let body = incoming.body ?? ""
        draftTitle = title
        draftBody = body
        lastSavedTitle = title
        lastSavedBody = body
        baseSha256 = incoming.stateSha256
        conflictDocument = nil
        showConflictDiff = false
        // Idle, not saved: nothing was written. Saying "Saved" here would
        // be the same lie in a new costume.
        status = .idle
        DocumentDraftStore.clear(for: document.id)
        draftPersistTask?.cancel()
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
        // Found live (2026-08-26): without this, DocumentDetailView's own
        // `loaded` — captured whenever the page first appeared — stays
        // exactly that stale after Cancel closes this sheet, so the reader
        // sees an OLDER body than the one they just looked at and chose to
        // adopt. `incoming` is not a write confirmation (`onSaved`'s own
        // doc comment is otherwise right to require one) but it IS a real,
        // just-fetched read of current server truth — the 409 response is
        // where it came from — so relaying it is correct on the same
        // "never show what is known to be stale" grounds as everywhere
        // else in this app, not a loosening of the write-confirmed
        // contract. Deliberately no `isPresented = false` alongside it —
        // this file's own header is explicit that "Use theirs instead"
        // must leave the sheet OPEN, unlike Keep-mine.
        onSaved(incoming)
    }

    // MARK: - Cancel / dismiss

    private func requestCancel() {
        if isDirty {
            showDiscardConfirm = true
        } else {
            dismissWithoutSaving()
        }
    }

    private func dismissWithoutSaving() {
        DocumentDraftStore.clear(for: document.id)
        draftPersistTask?.cancel()
        isPresented = false
    }

    // MARK: - Local draft persistence (disk only, never the network)

    private func scheduleDraftPersist() {
        draftPersistTask?.cancel()
        draftPersistTask = Task {
            try? await Task.sleep(for: .milliseconds(600))
            guard !Task.isCancelled else { return }
            persistDraftNow()
        }
    }

    private func persistDraftNow() {
        guard isDirty else {
            // Typed back to matching what is already saved — nothing left
            // worth protecting, and a stray earlier file must not linger
            // and get "restored" as a phantom edit next time.
            DocumentDraftStore.clear(for: document.id)
            return
        }
        DocumentDraftStore.save(
            DocumentDraftSnapshot(documentId: document.id, title: draftTitle, body: draftBody, baseSha256: baseSha256)
        )
    }
}

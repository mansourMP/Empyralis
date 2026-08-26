import SwiftUI

/// One document — rendered as markdown for everyone, with an explicit edit
/// affordance for whoever this app can confirm has write access. The list
/// row it was opened from carries no body (the list route omits it), so the
/// body is fetched here — and the row's own title is shown immediately
/// rather than behind a spinner, because the identity is already known and
/// only the content is outstanding.
///
/// EDITING lives in `DocumentEditSheet` — title + body together, explicit
/// Save, the stale-write conflict resolution that feature exists for. See
/// that file's own header for the autosave-vs-explicit-Save reasoning. This
/// file's job is reading, and deciding whether to offer the door into that
/// sheet at all.
///
/// THE EDIT AFFORDANCE IS A TOOLBAR PENCIL, NOT A TAP-ANYWHERE-IN-THE-BODY
/// gesture — a deliberate departure from the web's own click-to-edit-per-
/// region idiom (TaskDetailView.tsx/DocumentDetailView.tsx). MarkdownRenderer
/// renders REAL tappable links (SwiftUI `Text` + `AttributedString` link
/// runs, intercepted by the Text view itself, no `Button` wrapper). Wrapping
/// the whole rendered body in a tap gesture to "open the editor" would fight
/// that — SwiftUI's hit-testing gives an enclosing gesture priority over an
/// inner Text's own link recognition in exactly the cases that matter, and
/// getting that fully right (the web's own equivalent problem, solved there
/// with careful target-checking) is not worth the risk here when a standard
/// nav-bar pencil (the Notes/Reminders idiom) is unambiguous, fully
/// discoverable, and needs no gesture arbitration at all.
struct DocumentDetailView: View {
    let document: EmpDocument

    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var store: WorkspaceStore
    @Environment(\.colorScheme) private var scheme

    /// The full document, WITH body and the stale-write precondition token
    /// (`state_sha256`) — nil until `load()` completes. `document` (the
    /// list-row shape this view was opened with) carries neither, so
    /// editing is offered only once this is populated: you cannot safely
    /// edit what you have not actually read.
    @State private var loaded: EmpDocument?
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var showEditor = false

    /// What the header renders: the freshly loaded document once it
    /// exists, the list row's own identity fields before that — so title
    /// and path show immediately rather than behind a spinner, unchanged
    /// from this view's original behaviour.
    private var displayDocument: EmpDocument { loaded ?? document }

    /// WORKSPACE role only. See `DocumentAuthoring.canWrite`'s own doc
    /// comment for exactly how this differs from the web's per-PROJECT ACL
    /// (`useCanWriteProject`) and why that gap is a stated, accepted
    /// limitation of this pass rather than a silent one: a `member` who
    /// lacks membership in THIS document's specific project will still see
    /// the pencil here, tap it, and get a real, readable server refusal —
    /// never a silent no-op, but not the same precision the web has either.
    private var canWrite: Bool {
        DocumentAuthoring.canWrite(members: store.members, ownUserId: session.user?.id)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.x4) {
                header

                if let loaded {
                    let bodyText = loaded.body ?? ""
                    if bodyText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        Text("This document is empty.")
                            .font(.empSecondary)
                            .foregroundStyle(Theme.textMuted(scheme))
                    } else {
                        MarkdownRenderer(markdown: bodyText)
                    }
                } else if let errorMessage {
                    Text(errorMessage)
                        .font(.empSecondary)
                        .foregroundStyle(Theme.textSecondary(scheme))
                } else if isLoading {
                    ProgressView()
                        .frame(maxWidth: .infinity, alignment: .center)
                        .padding(.top, Space.x6)
                }
            }
            .padding(Space.x4)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(Theme.bgPage(scheme))
        // NO NAVIGATION TITLE — the body owns the title, exactly as
        // TaskDetailView does. Setting one here put the same string on
        // screen THREE times at once: the navigation bar, this view's own
        // header, and the document's leading `# H1`, which every real
        // markdown document has (all four seeded documents do). The
        // navigation-bar copy was also the one that truncated first, since
        // it is the narrowest of the three.
        .navigationBarTitleDisplayMode(.inline)
        //
        // THAT DECISION HAS A COST THIS SCREEN WAS PAYING FOR FREE UNTIL
        // NOW: `header` scrolls WITH the body (it lives inside the
        // ScrollView, not above it), so as a reader scrolls down, the
        // title+path pair passes directly under the status bar and the nav
        // bar's own toolbar (the edit pencil). This is the ONLY
        // `.inline`-mode screen in the app with no `.navigationTitle`
        // (grepped) — every other one (ProjectDetailView, AgentDetailView,
        // TaskDetailComponents, DocumentEditSheet…) gets a permanent bar
        // background for free, because SwiftUI paints a TITLED `.inline`
        // bar's chrome unconditionally but a TITLELESS one fully
        // transparent. So the title ghosted straight through behind the
        // clock/wifi/battery icons with nothing behind it — a real
        // collision, not a cosmetic one, and the exact defect reported
        // (and left open) after the 2026-08-26 parity sweep.
        //
        // Three fixes, weighed against this being the app's core READING
        // surface:
        //   1. Force the nav bar's own background material on
        //      (`.toolbarBackground(.visible, for: .navigationBar)`) — the
        //      choice below.
        //   2. PIN `header` above the ScrollView, the way ProjectDetailView
        //      pins its section Picker. Rejected: a Picker is a CONTROL
        //      that must stay reachable while scrolling; a document's
        //      title is not — a reader already knows what they opened.
        //      Spending a permanent ~70pt+ of THIS screen (`displayTitle`
        //      has no `.lineLimit`, so at large Dynamic Type it can wrap to
        //      two lines) is the wrong trade on the one screen where
        //      vertical room is the whole point.
        //   3. A native collapsing large title (`.navigationTitle` +
        //      `.large`, shrinking to compact-inline on scroll — the
        //      Apple/Linear idiom, and genuinely attractive: it buys a
        //      material AND reading room back). Rejected because it
        //      reopens the exact tripling defect the comment above already
        //      fixed on purpose: a real title here duplicates
        //      `displayTitle` a second time (it already renders in
        //      `header` and again as the document's leading `# H1`), and a
        //      collapsing title shows that duplicate AT REST, before any
        //      scrolling — worse than today's bug, not better.
        // (1) adds no text anywhere, so it cannot reintroduce the tripling
        // problem — it only supplies the backdrop every other titled
        // screen already gets automatically. Checked against the
        // pure-black dark theme specifically, since a translucent material
        // over `#000000` is exactly where a "grey band" would show up: the
        // system bar material composites to black here (screenshotted,
        // both themes — see the fix's own report), matching the material
        // this app already trusts for TaskDetailView/TaskComposerView's
        // own pinned composer bar (`.background(.bar)`).
        .toolbarBackground(.visible, for: .navigationBar)
        .toolbar {
            // Only once BOTH facts are known: this app can vouch for write
            // access, AND the full document (with a real precondition
            // token) is actually in hand. Neither alone is enough — CLAUDE.md's
            // "no dead controls": a pencil rendered before either resolves
            // would be a control that might not work.
            if canWrite && loaded != nil {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showEditor = true
                    } label: {
                        Image(systemName: "pencil")
                    }
                    .accessibilityLabel("Edit document")
                }
            }
        }
        .sheet(isPresented: $showEditor) {
            if let loaded {
                DocumentEditSheet(document: loaded, isPresented: $showEditor) { saved in
                    self.loaded = saved
                }
            }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: Space.x2) {
            Text(displayDocument.displayTitle)
                .font(.empTitle)
                .foregroundStyle(Theme.textPrimary(scheme))
            Text(displayDocument.path)
                .font(.empCaption)
                .foregroundStyle(Theme.textMuted(scheme))
            Divider()
                .overlay(Theme.border(scheme))
        }
    }

    private func load() async {
        guard let workspaceId = session.currentWorkspaceId else {
            isLoading = false
            return
        }
        isLoading = true
        defer { isLoading = false }
        do {
            let response: DocumentResponse = try await APIClient.shared.get(
                "/w/\(workspaceId)/fleet/documents/\(document.id)",
                query: [:]
            )
            if let fetched = response.document {
                loaded = fetched
                errorMessage = nil
            } else {
                errorMessage = response.error ?? "This document couldn't be opened."
            }
        } catch APIError.unauthorized {
            await session.handleUnauthorized()
        } catch {
            errorMessage = "Couldn't load this document. Pull to retry."
        }
    }
}

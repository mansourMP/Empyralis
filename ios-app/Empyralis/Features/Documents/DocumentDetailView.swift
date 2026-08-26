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
        // THAT DECISION HAS A COST: `header` scrolls WITH the body (it
        // lives inside the ScrollView, not above it), so as a reader
        // scrolls down, the title+path pair passes directly under the
        // status bar and the nav bar's own toolbar (the edit pencil) — and
        // it used to ghost through there with no backdrop, unlike every
        // other titled screen in the app (ProjectDetailView, AgentDetailView,
        // TaskDetailComponents…), which never has content reach that zone
        // in the first place (their own headers sit OUTSIDE the scroll
        // area, pinned).
        //
        // Three fixes were weighed:
        //   1. Give the nav bar an explicit, OPAQUE background
        //      (`.toolbarBackground(Theme.bgPage(scheme), for:
        //      .navigationBar)`) — chosen, see the measurement below.
        //   2. PIN `header` above the ScrollView, the way ProjectDetailView
        //      pins its section Picker. Rejected: a Picker is a CONTROL
        //      that must stay reachable while scrolling; a document's
        //      title is not — a reader already knows what they opened.
        //      Spending a permanent ~70pt+ of THIS screen (`displayTitle`
        //      has no `.lineLimit`, so at large Dynamic Type it can wrap
        //      to two lines) is the wrong trade on the one screen where
        //      vertical room is the whole point.
        //   3. A native collapsing large title (`.navigationTitle` +
        //      `.large`, shrinking to compact-inline on scroll — the
        //      Apple/Linear idiom). Genuinely attractive, since it buys a
        //      backdrop AND reading room back — but it reopens the exact
        //      tripling defect the comment above already fixed on
        //      purpose: a real title duplicates `displayTitle` a second
        //      time (it already renders in `header` and again as the
        //      document's leading `# H1`), and a collapsing title shows
        //      that duplicate AT REST, before any scrolling — worse than
        //      the original bug, not better.
        //
        // WHY THE STYLE MUST BE AN EXPLICIT COLOR, NOT JUST
        // `.toolbarBackground(.visible, for: .navigationBar)` — measured,
        // not assumed. On this build, a scrolled bar ALREADY shows a
        // background by default with no code at all (this is iOS 26's own
        // "Liquid Glass" behaviour, confirmed by giving this screen a real
        // `.navigationTitle` for a same-scroll-position A/B and seeing
        // zero change) — but that default is a thin, translucent material,
        // not an opaque one, so scrolled text still ghosts through it
        // legibly. `.toolbarBackground(.visible, for:)` alone changes
        // NOTHING: pixel-diffed against doing nothing at the identical
        // scroll offset, the two screenshots were identical but for the
        // status-bar clock. Passing `Theme.bgPage(scheme)` as the style
        // (kept paired with `.visible` so it also applies unconditionally
        // rather than only once the automatic scroll threshold trips)
        // produces a fully opaque backdrop instead — screenshotted, the
        // ghosting is completely gone, in both themes.
        .toolbarBackground(Theme.bgPage(scheme), for: .navigationBar)
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

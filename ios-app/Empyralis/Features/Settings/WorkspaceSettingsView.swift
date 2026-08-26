import SwiftUI
import UIKit

/// Settings ▸ Workspace — parity with the web's `SettingsShell`
/// `section === "workspace"` branch (`WorkspaceNameSection` +
/// `MembersSection` + `EmergencyStopSection`; Billing is deliberately NOT
/// ported — see the file-level scope note below).
///
/// Pushed from `SettingsView` (MainTabView.swift), so it does not own a
/// `NavigationStack` of its own.
///
/// SCOPE, decided before writing a line of this file: build member list +
/// invite, at parity with what the backend can actually promise today.
/// CLAUDE.md's own documented state on invites is why role-change and
/// member-removal are absent — there is no working route for either
/// (`create_workspace_invite_route` only ever creates a NEW pending row;
/// nothing revokes one, and nothing patches an existing member's role). A
/// control with no route behind it is a dead control, which this app's own
/// design law forbids more strongly than an absent screen ever costs.
/// Workspace rename is a real write surface of its own kind — left for the
/// web, matching the brief's "companion, not a second desktop" framing.
///
/// **Emergency Stop is the one exception, added later, and it earns the
/// exception rather than contradicting the framing above.** Every other
/// screen in this app is setup/observation done at a desk; a kill switch is
/// the opposite — it is precisely the control someone reaches for AWAY from
/// a keyboard, mid-incident, on the device already in their hand. Linear's
/// own "companion, purpose-designed for away-from-keyboard workflows"
/// positioning (see `README.md`) argues FOR this control on a phone, not
/// against it. It is placed here — one level into Settings ▸ Workspace,
/// exactly where the web puts it — rather than surfaced more prominently
/// (a toolbar action, a banner on Inbox): two navigation taps to reach the
/// screen plus a confirm step is the same depth a phone OS puts "erase this
/// device" or "sign out everywhere" behind, which is the right company for
/// a rare, destructive, fleet-wide action. A persistent stop button on the
/// app's daily-use front door would both overstate how often this is needed
/// and read as exactly the kind of surface this app's own law rejects — see
/// "A surface must earn its place" in the root CLAUDE.md.
struct WorkspaceSettingsView: View {
    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var store: WorkspaceStore
    @Environment(\.colorScheme) private var scheme

    // Invites are NOT in WorkspaceStore — nothing else in the app reads
    // them, so adding a fifth polled source to the shared store for one
    // settings screen would cost every other screen a round trip it never
    // needed. Fetched here, once, the same way HardwareSection/
    // McpServersSection own their reads independently on the web.
    // No loading flag: this section is hidden while empty regardless of
    // WHY (nothing pending yet vs. not fetched yet), matching
    // MembersSection.tsx's own gate (`!invitesLoading && invites.length >
    // 0`) — the web shows nothing during the fetch too. An error is the one
    // case that still has to say something even with zero rows, which is
    // what `invitesError` (kept below) is for.
    @State private var invites: [WorkspacePendingInvite] = []
    @State private var invitesError: String?

    @State private var inviteFormOpen = false
    @State private var inviteEmail = ""
    @State private var inviteRole = "member"
    @State private var isSendingInvite = false
    @State private var inviteError: String?
    @State private var lastInviteResult: InviteResult?
    @State private var copiedLink = false

    // Emergency stop. `nil` means "not yet loaded" (or the load failed) —
    // deliberately NOT persisted to WorkspaceStore/DiskCache, same reasoning
    // as the invites state above: nothing else in the app reads it, and this
    // is exactly the one place stale-cached "safe" state would be actively
    // misleading. `stopStateError` is set only when `stoppedState` is still
    // nil (a real load failure with nothing to fall back on); it never hides
    // the primary "Stop all agents" control — see `emergencyStopSection`.
    @State private var stoppedState: StoppedState?
    @State private var stopStateError: String?
    @State private var confirmingStop = false
    @State private var stopReason = ""
    @State private var isStopBusy = false
    @State private var stopActionError: String?

    private struct InviteResult: Equatable {
        let link: String
        let delivery: EmailDelivery?
    }

    private static let inviteRoles = ["viewer", "member", "owner"]

    private var canInvite: Bool {
        canInviteToWorkspace(members: store.members, ownUserId: session.user?.id)
    }

    var body: some View {
        // Mirrors SettingsView's own shell exactly (MainTabView.swift) —
        // the same page carrying two nested settings screens should not
        // read as two different products.
        ZStack {
            Theme.bgPage(scheme).ignoresSafeArea()

            // `.refreshable` has to sit on the scrollable view itself (the
            // List) — on the wrapping ZStack it is a no-op, since nothing
            // there is what actually scrolls.
            List {
                membersSection
                if canInvite { inviteSection }
                pendingInvitesSection
                emergencyStopSection
            }
            .scrollContentBackground(.hidden)
            .refreshable {
                await store.refresh()
                await loadInvites()
                await loadStopState()
            }
        }
        .navigationTitle("Workspace")
        .task { await loadInvites() }
        .task { await loadStopState() }
    }

    // MARK: - Members

    @ViewBuilder
    private var membersSection: some View {
        Section {
            if !store.hasLoadedOnce {
                ForEach(0..<3, id: \.self) { _ in
                    SkeletonRow()
                }
            } else if store.members.isEmpty {
                // "No members" and "couldn't find out" are different facts
                // — the same fourth-fact distinction WorkspaceStore already
                // keeps for agents (`agentsLookupFailed`), reused here via
                // `identityLookupFailed` since members ARE that lookup.
                if store.identityLookupFailed {
                    inlineNote(
                        icon: "exclamationmark.triangle",
                        title: "Couldn't load members",
                        message: "Pull down to try again.",
                        tone: Theme.offline(scheme)
                    )
                } else {
                    inlineNote(
                        icon: "person.2",
                        title: "No members yet",
                        message: "This workspace has no one on it.",
                        tone: Theme.textMuted(scheme)
                    )
                }
            } else {
                ForEach(store.members) { memberRow($0) }
            }
        } header: {
            Text("Members")
        } footer: {
            // Mirrors MembersSection.tsx's own subtitle, minus the
            // per-project detail — the phone has no project-membership
            // surface to point at.
            Text("Everyone with access to this workspace.")
        }
        .listRowBackground(Theme.bgCard(scheme))
    }

    private func memberRow(_ member: WorkspaceMember) -> some View {
        HStack(spacing: Space.x3) {
            Circle()
                .fill(Theme.bgInset(scheme))
                .overlay(Circle().stroke(Theme.border(scheme), lineWidth: 1))
                .overlay(
                    Text(member.initial)
                        // Deliberately NOT a scaling .emp* token. This sits
                        // inside a FIXED 28x28 circle; if the glyph grows
                        // with Dynamic Type but the badge doesn't, the
                        // letter outgrows its circle and overlaps the name
                        // beside it at large accessibility sizes. Matches
                        // ActorAvatar's own non-scaling letter treatment
                        // (Features/Tasks/TaskDetailComponents.swift) —
                        // same formula, size * 0.44 at .semibold, so a
                        // fixed-size avatar's initial is sized consistently
                        // everywhere in the app.
                        .font(.system(size: 28 * 0.44, weight: .semibold))
                        .foregroundStyle(Theme.textSecondary(scheme))
                )
                .frame(width: 28, height: 28)
                // A lone letter is not a word — Xcode's own accessibility
                // audit flags it as "Label not human-readable", and it is
                // redundant besides: the member's full name sits right next
                // to it in the same row. This is a plain HStack (not a
                // Button), so unlike PickerRow's leading-view pattern
                // nothing combines it into a composite label on its own —
                // hiding it is what keeps a VoiceOver swipe from stopping on
                // a bare "I" with no context before reaching the real name.
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 2) {
                Text(member.name)
                    .font(.empBody)
                    .foregroundStyle(Theme.textPrimary(scheme))
                if let email = member.email, !email.isEmpty, email != member.name {
                    Text(email)
                        .font(.empCaption)
                        .foregroundStyle(Theme.textMuted(scheme))
                }
            }

            Spacer()

            Text(workspaceRoleLabel(member.role))
                .font(.empCaptionMedium)
                .foregroundStyle(Theme.textSecondary(scheme))
                .padding(.horizontal, Space.x2)
                .padding(.vertical, 3)
                .background(Theme.bgInset(scheme), in: Capsule())
        }
        .padding(.vertical, 2)
    }

    // MARK: - Invite

    /// The invite TRIGGER carries this screen's one accent action — mirrors
    /// MembersSection.tsx exactly, where the outer "Invite" button is
    /// `fleet-btn--accent` and the form's own "Send invite" submit is
    /// deliberately neutral (its own comment: "the Invite trigger above
    /// already spends this view's one accent-filled action"). Two accent
    /// buttons in one view is the bug CLAUDE.md names outright.
    @ViewBuilder
    private var inviteSection: some View {
        Section {
            if inviteFormOpen {
                inviteForm
            } else {
                Button {
                    inviteFormOpen = true
                    inviteError = nil
                    lastInviteResult = nil
                } label: {
                    Label("Invite someone", systemImage: "person.badge.plus")
                        .font(.empBodyMedium)
                }
                .foregroundStyle(Theme.accent(scheme))
            }
        }
        .listRowBackground(Theme.bgCard(scheme))
    }

    private var trimmedInviteEmail: String {
        inviteEmail.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    @ViewBuilder
    private var inviteForm: some View {
        VStack(alignment: .leading, spacing: Space.x3) {
            // BOTH `TextField(_:text:)`'s placeholder AND the
            // `prompt:`-with-`.foregroundColor` variant render the
            // placeholder in system BLUE regardless — verified live on
            // device twice, not assumed. This is the same "left unset,
            // SwiftUI tints every control with Apple's default blue"
            // failure RootView's own comment already names, and neither of
            // SwiftUI's own placeholder mechanisms honours an override here
            // — so the placeholder is a plain, fully-owned `Text` overlay
            // instead, shown only while empty, with the TextField's own
            // built-in placeholder left blank so iOS has nothing left to
            // style on its own.
            ZStack(alignment: .leading) {
                if inviteEmail.isEmpty {
                    Text("teammate@example.com")
                        .font(.empBody)
                        .foregroundStyle(Theme.textMuted(scheme))
                }
                TextField("", text: $inviteEmail)
                    .keyboardType(.emailAddress)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .font(.empBody)
                    .foregroundStyle(Theme.textPrimary(scheme))
            }
            .padding(.horizontal, Space.x3)
            .padding(.vertical, 10)
            .background(Theme.bgField(scheme), in: RoundedRectangle(cornerRadius: Radius.control))
            .overlay(
                RoundedRectangle(cornerRadius: Radius.control)
                    .stroke(Theme.border(scheme), lineWidth: 1)
            )

            Picker("Role", selection: $inviteRole) {
                ForEach(Self.inviteRoles, id: \.self) { role in
                    Text(workspaceRoleLabel(role)).tag(role)
                }
            }
            .pickerStyle(.segmented)

            if let inviteError {
                Text(inviteError)
                    .font(.empCaption)
                    .foregroundStyle(Theme.offline(scheme))
            }

            if let lastInviteResult {
                inviteResultCard(lastInviteResult)
            }

            HStack {
                Button("Cancel") {
                    inviteFormOpen = false
                    inviteEmail = ""
                    inviteError = nil
                    lastInviteResult = nil
                }
                .font(.empBodyMedium)
                .foregroundStyle(Theme.textSecondary(scheme))

                Spacer()

                Button {
                    Task { await sendInvite() }
                } label: {
                    if isSendingInvite {
                        ProgressView()
                    } else {
                        Text("Send invite")
                    }
                }
                .font(.empBodyMedium)
                .foregroundStyle(trimmedInviteEmail.isEmpty ? Theme.textMuted(scheme) : Theme.textPrimary(scheme))
                .disabled(isSendingInvite || trimmedInviteEmail.isEmpty)
            }
        }
        .padding(.vertical, Space.x2)
    }

    private func inviteResultCard(_ result: InviteResult) -> some View {
        VStack(alignment: .leading, spacing: Space.x2) {
            Text(inviteDeliveryHint(result.delivery))
                .font(.empCaptionMedium)
                .foregroundStyle(Theme.textPrimary(scheme))

            HStack(spacing: Space.x2) {
                Text(result.link)
                    .font(.empMono)
                    .foregroundStyle(Theme.textSecondary(scheme))
                    .lineLimit(1)
                    .truncationMode(.middle)

                Spacer()

                Button {
                    UIPasteboard.general.string = result.link
                    copiedLink = true
                    Task {
                        try? await Task.sleep(for: .milliseconds(1500))
                        copiedLink = false
                    }
                } label: {
                    Image(systemName: copiedLink ? "checkmark" : "doc.on.doc")
                        .font(.system(size: 13))
                        .foregroundStyle(Theme.textSecondary(scheme))
                }
                // Icon-only, and an SF Symbol name alone ("doc on doc") tells
                // a VoiceOver user nothing about what the control does. The
                // label mirrors the visual state the checkmark already shows.
                .accessibilityLabel(copiedLink ? "Copied" : "Copy invite link")
            }
            .padding(Space.x2)
            .background(Theme.bgInset(scheme), in: RoundedRectangle(cornerRadius: Radius.control))

            // Matches MembersSection.tsx's own caption verbatim.
            Text("Only works for the email it was created for, and expires in 7 days.")
                .font(.empCaption)
                .foregroundStyle(Theme.textMuted(scheme))
        }
    }

    private func sendInvite() async {
        guard let workspaceId = session.currentWorkspaceId else { return }
        let email = trimmedInviteEmail.lowercased()
        guard !email.isEmpty else { return }
        isSendingInvite = true
        inviteError = nil
        defer { isSendingInvite = false }
        do {
            let response: CreateInviteResponse = try await APIClient.shared.post(
                "/workspaces/\(workspaceId)/invites",
                body: ["email": email, "role": inviteRole]
            )
            // No frontend origin to build a `https://<this build's host>/join/…`
            // link from — a DEBUG build's API base is a bare loopback port
            // with no paired web server, so unlike the web's
            // `window.location.origin`, there is genuinely no "here" to
            // point at. Production's own frontend is the one address this
            // token can always be redeemed against.
            lastInviteResult = InviteResult(
                link: "https://empyralis.ai/join/\(response.token)",
                delivery: response.emailDelivery
            )
            inviteEmail = ""
            await loadInvites()
        } catch APIError.unauthorized {
            await session.handleUnauthorized()
        } catch APIError.server(let message) {
            inviteError = message
        } catch APIError.decoding {
            // 2xx — APIClient only throws .decoding after its status guard
            // passes, so the invite is real server-side; only this reply
            // was unreadable. Claiming failure here would be the exact lie
            // CLAUDE.md's outcome-honesty law forbids. The row shows up in
            // Pending invites below instead of in the copy-link card.
            inviteEmail = ""
            await loadInvites()
        } catch {
            inviteError = "Could not create this invite."
        }
    }

    // MARK: - Pending invites

    @ViewBuilder
    private var pendingInvitesSection: some View {
        // Hidden entirely when there is nothing pending and nothing failed
        // — matches MembersSection.tsx's own `invites.length > 0` gate.
        // An error is shown even with zero rows, because "nothing pending"
        // and "couldn't find out" are different facts.
        if !invites.isEmpty || invitesError != nil {
            Section {
                if let invitesError, invites.isEmpty {
                    inlineNote(
                        icon: "exclamationmark.triangle",
                        title: "Couldn't load pending invites",
                        message: invitesError,
                        tone: Theme.offline(scheme)
                    )
                }
                ForEach(invites) { pendingInviteRow($0) }
            } header: {
                Text("Pending invites")
            }
            .listRowBackground(Theme.bgCard(scheme))
        }
    }

    private func pendingInviteRow(_ invite: WorkspacePendingInvite) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(invite.email)
                    .font(.empBody)
                    .foregroundStyle(Theme.textPrimary(scheme))
                if let date = invite.createdAt?.date {
                    Text("Invited \(relativeTimeString(date))")
                        .font(.empCaption)
                        .foregroundStyle(Theme.textMuted(scheme))
                } else {
                    Text("Invited")
                        .font(.empCaption)
                        .foregroundStyle(Theme.textMuted(scheme))
                }
            }

            Spacer()

            Text(workspaceRoleLabel(invite.role))
                .font(.empCaptionMedium)
                .foregroundStyle(Theme.textSecondary(scheme))
                .padding(.horizontal, Space.x2)
                .padding(.vertical, 3)
                .background(Theme.bgInset(scheme), in: Capsule())
        }
        .padding(.vertical, 2)
    }

    private func loadInvites() async {
        guard let workspaceId = session.currentWorkspaceId else { return }
        do {
            let response: WorkspaceInvitesResponse = try await APIClient.shared.get(
                "/workspaces/\(workspaceId)/invites"
            )
            invites = response.items
            invitesError = nil
        } catch APIError.unauthorized {
            await session.handleUnauthorized()
        } catch APIError.server(let message) {
            // Last-good rows stay on screen either way — same posture
            // WorkspaceStore already keeps for notifications/blocked runs.
            invitesError = message
        } catch {
            invitesError = "Couldn't load pending invites."
        }
    }

    // MARK: - Emergency stop

    /// Workspace-wide kill switch — every agent, every channel, immediately.
    /// See this file's own header for why it lives on this screen. Renders
    /// one of: a skeleton (never loaded yet), the stopped card (Resume), the
    /// confirm form, or the plain trigger — never more than one at a time,
    /// and the trigger still renders even when the GET below failed, so a
    /// flaky connection can never take the one control this section exists
    /// for away from someone who needs it.
    @ViewBuilder
    private var emergencyStopSection: some View {
        Section {
            if let stopActionError {
                Text(stopActionError)
                    .font(.empCaption)
                    .foregroundStyle(Theme.offline(scheme))
            }

            if stoppedState == nil && stopStateError == nil {
                SkeletonRow()
            } else if let stopped = stoppedState, stopped.active {
                stoppedCard(stopped)
            } else {
                if let stopStateError, stoppedState == nil {
                    // "Not stopped" and "could not find out" are different
                    // facts — the control below still renders either way;
                    // stopping is safe to attempt even without a confirmed
                    // prior state.
                    Text("Couldn't confirm whether agents are already stopped.")
                        .font(.empCaption)
                        .foregroundStyle(Theme.textMuted(scheme))
                }
                if confirmingStop {
                    confirmStopForm
                } else {
                    stopTriggerButton
                }
            }
        } header: {
            Text("Emergency stop")
        } footer: {
            // Mirrors EmergencyStopSection.tsx's own subtitle verbatim.
            Text("Immediately stops every agent in this workspace from replying, on every channel. Nothing is deleted — resume at any time to pick back up where they left off.")
        }
        .listRowBackground(Theme.bgCard(scheme))
    }

    /// Neutral weight, never the accent. Matches `MainTabView.swift`'s Sign
    /// Out precedent rather than the web's own `fleet-btn--accent` on
    /// "Confirm stop" — Theme.swift's accent law reserves violet for a
    /// view's single PRIMARY action, and a destructive control is never
    /// that; see `confirmStopForm` below for where the platform's own
    /// destructive-role red is used instead.
    private var stopTriggerButton: some View {
        Button {
            confirmingStop = true
            stopActionError = nil
        } label: {
            Label("Stop all agents", systemImage: "stop.circle")
                .font(.empBodyMedium)
        }
        .foregroundStyle(Theme.textPrimary(scheme))
    }

    @ViewBuilder
    private var confirmStopForm: some View {
        VStack(alignment: .leading, spacing: Space.x3) {
            Label("Stop every agent in this workspace?", systemImage: "exclamationmark.triangle")
                .font(.empBodyMedium)
                .foregroundStyle(Theme.textPrimary(scheme))

            Text("No one will get a reply from any agent here — on any channel — until you resume.")
                .font(.empCaption)
                .foregroundStyle(Theme.textMuted(scheme))

            // Same "own, fully-styled placeholder" workaround as the invite
            // email field above — TextField's built-in placeholder
            // mechanisms both render in system blue regardless of override.
            ZStack(alignment: .leading) {
                if stopReason.isEmpty {
                    Text("Reason (optional, visible in the activity log)")
                        .font(.empBody)
                        .foregroundStyle(Theme.textMuted(scheme))
                }
                TextField("", text: $stopReason)
                    .font(.empBody)
                    .foregroundStyle(Theme.textPrimary(scheme))
            }
            .padding(.horizontal, Space.x3)
            .padding(.vertical, 10)
            .background(Theme.bgField(scheme), in: RoundedRectangle(cornerRadius: Radius.control))
            .overlay(
                RoundedRectangle(cornerRadius: Radius.control)
                    .stroke(Theme.border(scheme), lineWidth: 1)
            )

            HStack {
                Button("Cancel") {
                    confirmingStop = false
                    stopReason = ""
                    stopActionError = nil
                }
                .font(.empBodyMedium)
                .foregroundStyle(Theme.textSecondary(scheme))
                .disabled(isStopBusy)

                Spacer()

                // `role: .destructive` — the platform's own red, the same
                // mechanism Sign Out uses (MainTabView.swift). Never the
                // brand accent for a destructive action.
                Button(role: .destructive) {
                    Task { await confirmStopAllAgents() }
                } label: {
                    if isStopBusy {
                        ProgressView()
                    } else {
                        Text("Confirm stop")
                    }
                }
                .font(.empBodyMedium)
                .disabled(isStopBusy)
            }
        }
        .padding(.vertical, Space.x2)
    }

    private func stoppedCard(_ stopped: StoppedState) -> some View {
        VStack(alignment: .leading, spacing: Space.x2) {
            Label("All agents stopped", systemImage: "stop.circle.fill")
                .font(.empBodyMedium)
                .foregroundStyle(Theme.offline(scheme))

            Text(stoppedDetailLine(stopped))
                .font(.empCaption)
                .foregroundStyle(Theme.textMuted(scheme))

            Button {
                Task { await resumeAllAgents() }
            } label: {
                if isStopBusy {
                    ProgressView()
                } else {
                    Label("Resume all agents", systemImage: "play.circle")
                }
            }
            .font(.empBodyMedium)
            .foregroundStyle(Theme.textPrimary(scheme))
            .disabled(isStopBusy)
        }
        .padding(.vertical, Space.x1)
    }

    /// "Stopped by X · 3 hr ago · "reason"" — mirrors
    /// EmergencyStopSection.tsx's own composed line exactly. `TaskDates.parse`
    /// (not a second date parser) already carries the space-separator /
    /// fractional-seconds cascade this backend's `at` field needs.
    private func stoppedDetailLine(_ stopped: StoppedState) -> String {
        let trimmedLabel = stopped.stoppedByLabel?.trimmingCharacters(in: .whitespaces)
        let who = (trimmedLabel?.isEmpty == false) ? trimmedLabel! : "an owner"
        var line = "Stopped by \(who)"
        if let at = stopped.at, let date = TaskDates.parse(at) {
            line += " · \(relativeTimeString(date))"
        }
        if let reason = stopped.reason?.trimmingCharacters(in: .whitespaces), !reason.isEmpty {
            line += " · \"\(reason)\""
        }
        return line
    }

    /// `GET /w/{id}/fleet/workspace` — viewer-reachable, so this always
    /// resolves for anyone who can even see this screen (unlike the two
    /// mutations below, which are owner-only).
    private func loadStopState() async {
        guard let workspaceId = session.currentWorkspaceId else { return }
        do {
            let response: FleetWorkspaceStopStateResponse = try await APIClient.shared.get(
                "/w/\(workspaceId)/fleet/workspace"
            )
            guard response.ok else {
                stopStateError = "Couldn't load the stop state."
                return
            }
            stoppedState = response.workspace?.stopped ?? StoppedState(active: false, reason: nil, stoppedByLabel: nil, at: nil)
            stopStateError = nil
        } catch APIError.unauthorized {
            await session.handleUnauthorized()
        } catch APIError.server(let message) {
            stopStateError = message
        } catch {
            stopStateError = "Couldn't load whether agents are stopped."
        }
    }

    /// `POST /w/{id}/fleet/stop-all`. THREE outcomes, matching this file's
    /// own `sendInvite()` pattern and the root CLAUDE.md's outcome-honesty
    /// law — never collapsed into a plain "stopped" or "failed":
    ///
    ///   ok:false            the server READ the request and refused (e.g. a
    ///                       non-owner caller). Show its own reason verbatim.
    ///   2xx, undecodable    the stop LANDED server-side — APIClient only
    ///                       throws .decoding after its 2xx guard passes.
    ///                       Re-fetch the real state rather than claim a
    ///                       failure that did not happen.
    ///   no response at all  we genuinely cannot tell. Say so — never claim
    ///                       "stopped" (the worst possible lie here) and
    ///                       never claim "failed" outright, which could send
    ///                       someone hunting for a laptop for a stop that
    ///                       may have already landed.
    private func confirmStopAllAgents() async {
        guard let workspaceId = session.currentWorkspaceId else {
            stopActionError = "No workspace."
            return
        }
        isStopBusy = true
        stopActionError = nil
        defer { isStopBusy = false }
        do {
            let ack: FleetStopControlAck = try await APIClient.shared.post(
                "/w/\(workspaceId)/fleet/stop-all",
                body: ["reason": stopReason.trimmingCharacters(in: .whitespacesAndNewlines)]
            )
            guard ack.ok else {
                stopActionError = ack.error ?? "Couldn't stop all agents."
                return
            }
            stoppedState = ack.stopped ?? StoppedState(active: true, reason: nil, stoppedByLabel: nil, at: nil)
            stopStateError = nil
            confirmingStop = false
            stopReason = ""
        } catch APIError.unauthorized {
            await session.handleUnauthorized()
        } catch APIError.server(let message) {
            stopActionError = message
        } catch APIError.decoding {
            confirmingStop = false
            stopReason = ""
            await loadStopState()
        } catch {
            stopActionError = "Couldn't confirm whether that stopped. Check your connection and try again."
        }
    }

    /// `POST /w/{id}/fleet/resume-all`. Same three-outcome shape as the stop
    /// above, kept as its own function rather than a shared helper — the
    /// optimistic values differ (`active: true` vs `active: false`) and both
    /// are short enough that sharing would cost more clarity than it saves.
    private func resumeAllAgents() async {
        guard let workspaceId = session.currentWorkspaceId else {
            stopActionError = "No workspace."
            return
        }
        isStopBusy = true
        stopActionError = nil
        defer { isStopBusy = false }
        do {
            let ack: FleetStopControlAck = try await APIClient.shared.post(
                "/w/\(workspaceId)/fleet/resume-all", body: [:]
            )
            guard ack.ok else {
                stopActionError = ack.error ?? "Couldn't resume agents."
                return
            }
            stoppedState = ack.stopped ?? StoppedState(active: false, reason: nil, stoppedByLabel: nil, at: nil)
            stopStateError = nil
        } catch APIError.unauthorized {
            await session.handleUnauthorized()
        } catch APIError.server(let message) {
            stopActionError = message
        } catch APIError.decoding {
            await loadStopState()
        } catch {
            stopActionError = "Couldn't confirm whether that resumed. Check your connection and try again."
        }
    }

    // MARK: - Shared bits

    private func inlineNote(icon: String, title: String, message: String, tone: Color) -> some View {
        HStack(alignment: .top, spacing: Space.x3) {
            Image(systemName: icon)
                .font(.system(size: 14))
                .foregroundStyle(tone)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.empBodyMedium)
                    .foregroundStyle(Theme.textPrimary(scheme))
                Text(message)
                    .font(.empCaption)
                    .foregroundStyle(Theme.textMuted(scheme))
            }
        }
        .padding(.vertical, Space.x1)
    }

    /// A tiny Date-based relative formatter, deliberately separate from
    /// `TaskDates.timeAgo` (which parses a STRING). `InviteTimestamp`
    /// already resolves to a `Date` — reparsing it back into a string just
    /// to hand it to a string-first API would be the odd choice here.
    private func relativeTimeString(_ date: Date) -> String {
        let interval = Date().timeIntervalSince(date)
        if interval < 60 { return "just now" }
        if interval < 7 * 24 * 3600 {
            let f = RelativeDateTimeFormatter()
            f.unitsStyle = .abbreviated
            return f.localizedString(for: date, relativeTo: Date())
        }
        let f = DateFormatter()
        f.dateStyle = .medium
        f.timeStyle = .none
        return f.string(from: date)
    }
}

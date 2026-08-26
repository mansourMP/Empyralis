import SwiftUI
import UIKit

/// Settings ▸ Workspace — parity with the web's `SettingsShell`
/// `section === "workspace"` branch (`WorkspaceNameSection` +
/// `MembersSection`; Billing and Emergency Stop are deliberately NOT ported
/// — see the file-level scope note below).
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
/// Workspace rename and the workspace-wide Emergency Stop are both real
/// write surfaces of their own kind (and Emergency Stop is a destructive
/// fleet-wide action) — left for the web, matching the brief's "companion,
/// not a second desktop" framing.
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
            }
            .scrollContentBackground(.hidden)
            .refreshable {
                await store.refresh()
                await loadInvites()
            }
        }
        .navigationTitle("Workspace")
        .task { await loadInvites() }
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
                        .font(.empCaptionMedium)
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

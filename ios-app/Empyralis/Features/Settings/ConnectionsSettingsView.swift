import SwiftUI

/// Settings ▸ Connections — READ-ONLY parity with the web's `SettingsShell`
/// `section === "connections"` branch, narrowed on purpose.
///
/// Pushed from `SettingsView` (MainTabView.swift), so it does not own a
/// `NavigationStack` of its own.
///
/// SCOPE. The web section stacks FOUR pieces: `HardwareSection` (pairing +
/// provisioning), `McpServersSection` (connect-by-URL + per-tool
/// approve/deny), `McpApiKeysSection` (mint bearer keys for EXTERNAL MCP
/// clients to call INTO this workspace), `ChannelPairingSection` (Slack/SMS/
/// WeChat sender pairing). This screen builds the two that are genuinely
/// "what's connected, and its status" — Hardware and MCP servers — and
/// stops there, on the brief's own instruction: no pairing flow (a
/// `curl | bash` on a real machine is not a phone task), and read-only.
///
/// `McpApiKeysSection` and `ChannelPairingSection` are NOT ported. Both are
/// mint-a-credential flows (an API key; a pairing code), not "what's
/// connected" — showing them here without the ability to complete or revoke
/// either would be exactly the kind of half-surface CLAUDE.md's "a dead
/// control is worse than an absent screen" law warns against.
struct ConnectionsSettingsView: View {
    @EnvironmentObject private var session: SessionStore
    @Environment(\.colorScheme) private var scheme

    @State private var gateways: [GatewayRegistration] = []
    @State private var hasLoadedGatewaysOnce = false
    @State private var gatewaysError: String?

    @State private var mcpServers: [McpServerRecord] = []
    @State private var hasLoadedMcpOnce = false
    @State private var mcpError: String?

    var body: some View {
        ZStack {
            Theme.bgPage(scheme).ignoresSafeArea()

            List {
                hardwareSection
                mcpSection
            }
            .scrollContentBackground(.hidden)
            .refreshable {
                async let a: Void = loadGateways()
                async let b: Void = loadMcpServers()
                _ = await (a, b)
            }
        }
        .navigationTitle("Connections")
        .task {
            async let a: Void = loadGateways()
            async let b: Void = loadMcpServers()
            _ = await (a, b)
        }
    }

    // MARK: - Hardware

    @ViewBuilder
    private var hardwareSection: some View {
        Section {
            if !hasLoadedGatewaysOnce {
                ForEach(0..<2, id: \.self) { _ in SkeletonRow() }
            } else if gateways.isEmpty {
                if let gatewaysError {
                    inlineNote(
                        icon: "exclamationmark.triangle",
                        title: "Couldn't load your computers",
                        message: gatewaysError,
                        tone: Theme.offline(scheme)
                    )
                } else {
                    // Mirrors HardwareSection.tsx's own empty-state copy —
                    // instructive rather than a bare "nothing here", the
                    // house rule for empty states that have something real
                    // to teach.
                    inlineNote(
                        icon: "desktopcomputer",
                        title: "No computers connected",
                        message: "Agents here can still write tasks and documents — they just can't run commands, open files, or use a browser.",
                        tone: Theme.textMuted(scheme)
                    )
                }
            } else {
                ForEach(gateways) { gatewayRow($0) }
            }
        } header: {
            Text("Hardware")
        }
        .listRowBackground(Theme.bgCard(scheme))
    }

    private func gatewayRow(_ g: GatewayRegistration) -> some View {
        let presentation = gatewayConnectionPresentation(g.connectionStatus ?? g.status)
        let name = (g.displayName?.isEmpty == false ? g.displayName : nil)
            ?? (g.hardwareLabel?.isEmpty == false ? g.hardwareLabel : nil)
            ?? g.platform ?? "Computer"

        return HStack(spacing: Space.x3) {
            RoundedRectangle(cornerRadius: 6)
                .fill(Theme.bgInset(scheme))
                .frame(width: 28, height: 28)
                .overlay(
                    Image(systemName: g.hardwareKind == "cloud_vps" ? "server.rack" : "desktopcomputer")
                        .font(.system(size: 13))
                        .foregroundStyle(Theme.textSecondary(scheme))
                )

            VStack(alignment: .leading, spacing: 2) {
                Text(name)
                    .font(.empBody)
                    .foregroundStyle(Theme.textPrimary(scheme))
                Text(hardwareDetailLine(g))
                    .font(.empCaption)
                    .foregroundStyle(Theme.textMuted(scheme))
            }

            Spacer()

            VStack(alignment: .trailing, spacing: 2) {
                Text(presentation.label)
                    .font(.empCaptionMedium)
                    .foregroundStyle(toneColor(presentation.tone))
                if let version = g.gatewayVersion, !version.isEmpty {
                    Text("v\(version)")
                        .font(.empCaption)
                        .foregroundStyle(Theme.textMuted(scheme))
                }
            }
        }
        .padding(.vertical, 2)
    }

    /// "Cloud · DigitalOcean · last seen 2 min ago" — `TaskDates.timeAgo`
    /// already returns `""` for an unparseable/missing stamp, so appending
    /// it is safe without a second parse just to check.
    private func hardwareDetailLine(_ g: GatewayRegistration) -> String {
        let type = hardwareTypeLine(g)
        guard let lastSeenAt = g.lastSeenAt else { return type }
        let ago = TaskDates.timeAgo(lastSeenAt)
        return ago.isEmpty ? type : "\(type) · last seen \(ago)"
    }

    private func loadGateways() async {
        guard let workspaceId = session.currentWorkspaceId else { return }
        do {
            let response: GatewayRegistrationsResponse = try await APIClient.shared.get(
                "/gateway/registrations", query: ["workspace_id": workspaceId]
            )
            gateways = response.items
            gatewaysError = nil
        } catch APIError.unauthorized {
            await session.handleUnauthorized()
        } catch APIError.server(let message) {
            gatewaysError = message
        } catch {
            gatewaysError = "Couldn't load your connected computers."
        }
        hasLoadedGatewaysOnce = true
    }

    // MARK: - MCP servers

    @ViewBuilder
    private var mcpSection: some View {
        Section {
            if !hasLoadedMcpOnce {
                ForEach(0..<2, id: \.self) { _ in SkeletonRow() }
            } else if mcpServers.isEmpty {
                if let mcpError {
                    inlineNote(
                        icon: "exclamationmark.triangle",
                        title: "Couldn't load MCP servers",
                        message: mcpError,
                        tone: Theme.offline(scheme)
                    )
                } else {
                    inlineNote(
                        icon: "network",
                        title: "No MCP servers connected",
                        message: "Connect one from the web app to give this workspace's agents new tools.",
                        tone: Theme.textMuted(scheme)
                    )
                }
            } else {
                ForEach(mcpServers) { mcpRow($0) }
            }
        } header: {
            Text("MCP servers")
        }
        .listRowBackground(Theme.bgCard(scheme))
    }

    private func mcpRow(_ server: McpServerRecord) -> some View {
        HStack(spacing: Space.x3) {
            RoundedRectangle(cornerRadius: 6)
                .fill(Theme.bgInset(scheme))
                .frame(width: 28, height: 28)
                .overlay(
                    Image(systemName: "network")
                        .font(.system(size: 12))
                        .foregroundStyle(Theme.textSecondary(scheme))
                )

            VStack(alignment: .leading, spacing: 2) {
                Text(server.label)
                    .font(.empBody)
                    .foregroundStyle(Theme.textPrimary(scheme))
                Text(mcpServerHost(server.endpoint))
                    .font(.empCaption)
                    .foregroundStyle(Theme.textMuted(scheme))
            }

            Spacer()

            Text(mcpServerStatusLabel(server))
                .font(.empCaptionMedium)
                .foregroundStyle(server.enabled ? Theme.textSecondary(scheme) : Theme.textMuted(scheme))
                .multilineTextAlignment(.trailing)
                .frame(maxWidth: 140, alignment: .trailing)
        }
        .padding(.vertical, 2)
    }

    private func loadMcpServers() async {
        guard let workspaceId = session.currentWorkspaceId else { return }
        do {
            let response: McpServersResponse = try await APIClient.shared.get(
                "/agent-registry/mcp/servers", query: ["workspace_id": workspaceId]
            )
            mcpServers = response.items
            mcpError = nil
        } catch APIError.unauthorized {
            await session.handleUnauthorized()
        } catch APIError.server(let message) {
            mcpError = message
        } catch {
            mcpError = "Couldn't load MCP servers."
        }
        hasLoadedMcpOnce = true
    }

    // MARK: - Shared bits

    private func toneColor(_ tone: ConnectionTone) -> Color {
        switch tone {
        case .online: return Theme.online(scheme)
        case .degraded: return Theme.warning(scheme)
        case .offline: return Theme.textMuted(scheme)
        case .error: return Theme.offline(scheme)
        }
    }

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
}

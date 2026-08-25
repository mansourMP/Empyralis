import XCTest
@testable import Empyralis

/// Settings ▸ Workspace / Connections — decode + pure-logic tests.
///
/// THE INVITE AND MCP-SERVER FIXTURES ARE VERBATIM CAPTURES from the seeded
/// disposable backend (2026-08-25, `http://127.0.0.1:8001`, workspace
/// `ws_35d35c8165e6`), not hand-typed from the model's own field names —
/// the same discipline `AgentDecodingTests` documents and for the same
/// reason: a hand-written fixture cannot catch a key mismatch, because
/// whoever writes it writes the keys the model already expects.
///
/// The ONE exception is `liveGatewayItemFixture` below, and its own comment
/// says so: the seeded workspace has zero paired gateways (no real machine
/// handshake was available to this pass), so that single fixture is built
/// from reading `gateway_registration_public_payload`'s source directly
/// (server_modules/gateway_registry_service.py:330-431) rather than from a
/// live capture — file:line cited so it can be re-verified against the
/// producer, never against this test's own memory of it.
final class SettingsModelsTests: XCTestCase {

    // MARK: - Invites — verbatim captures

    /// `GET /api/workspaces/ws_35d35c8165e6/invites`, after one real invite
    /// was created through `POST .../invites` against the seeded backend.
    private let liveInvitesListPayload = """
    {
      "items": [
        {
          "id": "invite_f70210bde036498cbd043971f8730954",
          "workspace_id": "ws_35d35c8165e6",
          "email": "ios.settings.verify@example.com",
          "role": "member",
          "status": "pending",
          "invited_by_user_id": "379f56d3-cff0-41ae-9734-3cec3a85b3c0",
          "created_at": 1787665601
        }
      ]
    }
    """.data(using: .utf8)!

    /// `POST /api/workspaces/ws_35d35c8165e6/invites` against the same
    /// seeded (unverified-email) account — real response, including the
    /// fourth delivery state (`withheld_unverified_sender`), which is
    /// impossible to reach with a hand-written fixture unless the author
    /// already knows the exact string the backend emits.
    private let liveCreateInvitePayload = """
    {
      "invite": {
        "id": "invite_f70210bde036498cbd043971f8730954",
        "workspace_id": "ws_35d35c8165e6",
        "email": "ios.settings.verify@example.com",
        "role": "member",
        "status": "pending",
        "project_id": "project_2e436d15240744b2",
        "created_at": 1787665601
      },
      "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IndvcmtzcGFjZV9pbnZpdGVfdjEifQ.eyJ0eXAiOiJ3b3Jrc3BhY2VfaW52aXRlX3YxIn0.18kQ1ZcCJNPVSIM3xzeWjb2WVpE1EVwwrgq5mUYJadM",
      "expires_at": 1788270401,
      "email_delivery": {
        "status": "withheld_unverified_sender",
        "email": "ios.settings.verify@example.com"
      }
    }
    """.data(using: .utf8)!

    func testDecodesTheRealInvitesListResponse() throws {
        let decoded = try JSONDecoder().decode(WorkspaceInvitesResponse.self, from: liveInvitesListPayload)
        XCTAssertEqual(decoded.items.count, 1)
        let invite = try XCTUnwrap(decoded.items.first)
        XCTAssertEqual(invite.email, "ios.settings.verify@example.com")
        XCTAssertEqual(invite.role, "member")
        XCTAssertEqual(invite.status, "pending")
    }

    /// `created_at` on this route is a bare JSON NUMBER (epoch seconds),
    /// not a string — the specific mismatch class this whole file exists to
    /// catch. Proving the raw capture carries a number, then that
    /// `InviteTimestamp` still resolves a real Date from it.
    func testInviteCreatedAtIsANumberAndDecodesToTheRightDay() throws {
        let raw = try XCTUnwrap(JSONSerialization.jsonObject(with: liveInvitesListPayload) as? [String: Any])
        let items = try XCTUnwrap(raw["items"] as? [[String: Any]])
        XCTAssertTrue(items[0]["created_at"] is NSNumber, "the capture must keep the route's real (numeric) shape")

        let decoded = try JSONDecoder().decode(WorkspaceInvitesResponse.self, from: liveInvitesListPayload)
        let date = try XCTUnwrap(decoded.items.first?.createdAt?.date)
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(secondsFromGMT: 0)!
        let c = cal.dateComponents([.year, .month, .day], from: date)
        XCTAssertEqual(c.year, 2026)
        XCTAssertEqual(c.month, 8)
        // 1787665601 is 2026-08-25 (epoch seconds) — not 1970, which a
        // seconds-read-as-milliseconds bug would produce.
        XCTAssertEqual(c.day, 25)
    }

    func testDecodesTheRealCreateInviteResponse() throws {
        let decoded = try JSONDecoder().decode(CreateInviteResponse.self, from: liveCreateInvitePayload)
        XCTAssertEqual(decoded.invite.email, "ios.settings.verify@example.com")
        XCTAssertEqual(decoded.invite.role, "member")
        XCTAssertFalse(decoded.token.isEmpty)
        XCTAssertEqual(decoded.emailDelivery?.status, "withheld_unverified_sender")
    }

    func testInviteDeliveryHintCoversAllFourStates() {
        XCTAssertEqual(
            inviteDeliveryHint(EmailDelivery(status: "sent", email: "a@b.com")),
            "Invite sent to a@b.com."
        )
        XCTAssertEqual(
            inviteDeliveryHint(EmailDelivery(status: "not_configured", email: "a@b.com")),
            "Email isn't set up here — share this link instead."
        )
        XCTAssertEqual(
            inviteDeliveryHint(EmailDelivery(status: "withheld_unverified_sender", email: "a@b.com")),
            "Verify your own email to send invites by email — share this link for now."
        )
        XCTAssertEqual(
            inviteDeliveryHint(EmailDelivery(status: "failed", email: "a@b.com")),
            "The invite email didn't send — share this link instead."
        )
        // Absent entirely (a response shape from before the mailer wiring,
        // per members-data.ts's own comment) must NOT read as success — the
        // dangerous default is claiming a send that never happened.
        XCTAssertEqual(
            inviteDeliveryHint(nil),
            "The invite email didn't send — share this link instead."
        )
    }

    func testCanInviteIsOwnerOnly() {
        let owner = WorkspaceMember(userId: "u1", email: "o@x.com", displayName: "Owner", role: "owner")
        let member = WorkspaceMember(userId: "u2", email: "m@x.com", displayName: "Member", role: "member")
        XCTAssertTrue(canInviteToWorkspace(members: [owner, member], ownUserId: "u1"))
        XCTAssertFalse(canInviteToWorkspace(members: [owner, member], ownUserId: "u2"))
        // No membership row for this account at all — fail closed, never
        // fail open on an absent fact.
        XCTAssertFalse(canInviteToWorkspace(members: [owner, member], ownUserId: nil))
        XCTAssertFalse(canInviteToWorkspace(members: [owner, member], ownUserId: "u-does-not-exist"))
    }

    // MARK: - Hardware (gateways)

    /// `GET /api/gateway/registrations?workspace_id=ws_35d35c8165e6` against
    /// the seeded backend — real, and really empty (no machine has been
    /// paired to this test workspace). Proves the ENVELOPE shape
    /// (`items`), which is the one thing every response — empty or not —
    /// shares.
    private let liveEmptyGatewaysPayload = """
    {"workspace_id": "ws_35d35c8165e6", "count": 0, "items": []}
    """.data(using: .utf8)!

    /// NOT a live capture — no gateway is paired in the seeded workspace,
    /// so there is nothing to capture. Built from reading
    /// `gateway_registration_public_payload` directly
    /// (server_modules/gateway_registry_service.py:388-431), plus its two
    /// sub-functions `_gateway_connection_payload` (:151-162,
    /// `connection_status`) and `_hardware_presentation` (:197-211,
    /// `hardware_kind`/`hardware_label`/`hardware_provider`) — file:line
    /// cited so this can be re-verified against the producer rather than
    /// against this comment. Carries extra real keys beyond what
    /// `GatewayRegistration` decodes (capabilities, metadata, …) on
    /// purpose: a narrow model must still decode a REALISTIC full envelope,
    /// not just the trimmed shape it expects.
    private let sourceDerivedGatewayItemFixture = """
    {
      "gateway_id": "gw_gt_studio_mac",
      "device_id": "device_abc123",
      "tenant_id": "tenant_c3c55bdb14f2",
      "workspace_id": "ws_35d35c8165e6",
      "user_id": "379f56d3-cff0-41ae-9734-3cec3a85b3c0",
      "status": "active",
      "device_trust_state": "trusted",
      "display_name": "Studio Mac",
      "platform": "darwin-arm64",
      "metadata": {},
      "runtime_access_mode": "default_guarded",
      "runtime_access_label": "Guarded",
      "project_sharing_opt_in": false,
      "capabilities": ["shell.execute", "filesystem.read_write"],
      "connection_status": "online",
      "heartbeat_fresh": true,
      "hardware_kind": "personal_device",
      "hardware_label": "This Device",
      "created_at": "2026-08-20T09:00:00.000000+00:00",
      "updated_at": "2026-08-25T13:00:00.000000+00:00",
      "last_seen_at": "2026-08-25T13:47:35.000000+00:00",
      "last_heartbeat_at": "2026-08-25T13:47:35.000000+00:00",
      "gateway_version": "0.1.0",
      "latest_gateway_version": "0.1.0",
      "gateway_update_available": false
    }
    """.data(using: .utf8)!

    func testDecodesTheRealEmptyGatewaysResponse() throws {
        let decoded = try JSONDecoder().decode(GatewayRegistrationsResponse.self, from: liveEmptyGatewaysPayload)
        XCTAssertEqual(decoded.items.count, 0)
    }

    func testDecodesASourceVerifiedPopulatedGatewayItem() throws {
        let decoded = try JSONDecoder().decode(GatewayRegistration.self, from: sourceDerivedGatewayItemFixture)
        XCTAssertEqual(decoded.gatewayId, "gw_gt_studio_mac")
        XCTAssertEqual(decoded.displayName, "Studio Mac")
        XCTAssertEqual(decoded.platform, "darwin-arm64")
        XCTAssertEqual(decoded.hardwareKind, "personal_device")
        XCTAssertEqual(decoded.connectionStatus, "online")
        XCTAssertEqual(decoded.gatewayVersion, "0.1.0")
    }

    /// Ports gateway-box-picker.tsx's `connectionPresentation` vocabulary
    /// exactly (source-read, not guessed) — the web's own comment on that
    /// function: "the one place a box's live reachability becomes a
    /// StatusChip tone+label ... so the same box never reads differently."
    func testGatewayConnectionPresentationMatchesTheWebVocabulary() {
        XCTAssertEqual(gatewayConnectionPresentation("online").label, "Online")
        XCTAssertEqual(gatewayConnectionPresentation("online").tone, .online)
        XCTAssertEqual(gatewayConnectionPresentation("execution_blocked").label, "Online — tools unavailable")
        XCTAssertEqual(gatewayConnectionPresentation("execution_blocked").tone, .degraded)
        XCTAssertEqual(gatewayConnectionPresentation("degraded").tone, .degraded)
        XCTAssertEqual(gatewayConnectionPresentation("reconnecting").tone, .degraded)
        XCTAssertEqual(gatewayConnectionPresentation("revoked").label, "Revoked")
        XCTAssertEqual(gatewayConnectionPresentation("revoked").tone, .error)
        // Unknown/absent falls to the same "Offline" floor the web uses —
        // never guessed as healthy.
        XCTAssertEqual(gatewayConnectionPresentation(nil).label, "Offline")
        XCTAssertEqual(gatewayConnectionPresentation("").tone, .offline)
        XCTAssertEqual(gatewayConnectionPresentation("something-new").tone, .offline)
    }

    func testHardwareTypeLine() {
        let cloud = GatewayRegistration(
            gatewayId: "g1", displayName: nil, platform: "linux",
            hardwareKind: "cloud_vps", hardwareLabel: "DigitalOcean · San Francisco 3",
            hardwareProvider: "digitalocean", connectionStatus: "online", status: "active",
            lastSeenAt: nil, gatewayVersion: nil
        )
        XCTAssertEqual(hardwareTypeLine(cloud), "Cloud · Digitalocean")

        let mac = GatewayRegistration(
            gatewayId: "g2", displayName: nil, platform: "darwin-arm64",
            hardwareKind: "personal_device", hardwareLabel: "This Device",
            hardwareProvider: nil, connectionStatus: "online", status: "active",
            lastSeenAt: nil, gatewayVersion: nil
        )
        XCTAssertEqual(hardwareTypeLine(mac), "Local computer")

        let sshRemote = GatewayRegistration(
            gatewayId: "g3", displayName: nil, platform: "linux",
            hardwareKind: "personal_device", hardwareLabel: "This Device",
            hardwareProvider: nil, connectionStatus: "online", status: "active",
            lastSeenAt: nil, gatewayVersion: nil
        )
        XCTAssertEqual(hardwareTypeLine(sshRemote), "Computer", "a non-darwin device must not be called 'Local'")
    }

    // MARK: - MCP servers — verbatim captures

    /// `GET /api/agent-registry/mcp/servers?workspace_id=ws_35d35c8165e6`
    /// before anything was connected.
    private let liveEmptyMcpPayload = """
    {"advanced_only": true, "items": []}
    """.data(using: .utf8)!

    /// The SAME endpoint after `PUT /api/agent-registry/mcp/servers/
    /// verify-test-server` — a real row this pass created on the seeded
    /// backend to prove the populated shape, byte-for-byte from the response.
    private let liveMcpListPayload = """
    {
      "advanced_only": true,
      "items": [
        {
          "id": "verify-test-server",
          "label": "Verify Test Server",
          "transport": "streamable_http",
          "endpoint": "https://example.com/mcp",
          "enabled": true,
          "advanced_only": true,
          "credential_id": null,
          "status": "ok",
          "status_detail": null,
          "status_updated_at": null,
          "tools": [],
          "metadata": {},
          "last_synced_at": null,
          "created_at": "2026-08-25T13:47:35.608422Z",
          "updated_at": "2026-08-25T13:47:35.608467Z",
          "tool_count": 0,
          "skill_ids": []
        }
      ]
    }
    """.data(using: .utf8)!

    func testDecodesTheRealEmptyMcpServersResponse() throws {
        let decoded = try JSONDecoder().decode(McpServersResponse.self, from: liveEmptyMcpPayload)
        XCTAssertEqual(decoded.items.count, 0)
    }

    func testDecodesTheRealPopulatedMcpServersResponse() throws {
        let decoded = try JSONDecoder().decode(McpServersResponse.self, from: liveMcpListPayload)
        let server = try XCTUnwrap(decoded.items.first)
        XCTAssertEqual(server.id, "verify-test-server")
        XCTAssertEqual(server.label, "Verify Test Server")
        XCTAssertEqual(server.endpoint, "https://example.com/mcp")
        XCTAssertTrue(server.enabled)
        XCTAssertEqual(server.toolCount, 0)
        XCTAssertEqual(server.status, "ok")
        XCTAssertNil(server.lastSyncedAt)
    }

    /// The exact date shape this real capture carries — ISO-T, SIX
    /// fractional digits, `Z` zone designator — is a THIRD variant beyond
    /// the two `TaskDates`'s own tests already pin (space-separated, and
    /// T-separated with a `+00:00` offset). `TaskDates.parse` must handle
    /// it too, since this screen reuses it for MCP/gateway timestamps
    /// rather than growing a fourth parser.
    func testTaskDatesParsesTheMcpServerTimestampShape() throws {
        let date = try XCTUnwrap(TaskDates.parse("2026-08-25T13:47:35.608422Z"))
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(secondsFromGMT: 0)!
        let c = cal.dateComponents([.year, .month, .day, .hour, .minute, .second], from: date)
        XCTAssertEqual(c.year, 2026); XCTAssertEqual(c.month, 8); XCTAssertEqual(c.day, 25)
        XCTAssertEqual(c.hour, 13); XCTAssertEqual(c.minute, 47); XCTAssertEqual(c.second, 35)
    }

    func testMcpServerStatusLabel() {
        func server(enabled: Bool = true, toolCount: Int = 0, status: String? = "ok", lastSyncedAt: String? = nil) -> McpServerRecord {
            McpServerRecord(id: "s", label: "S", endpoint: "https://x.example/mcp", enabled: enabled, toolCount: toolCount, status: status, lastSyncedAt: lastSyncedAt)
        }
        XCTAssertEqual(mcpServerStatusLabel(server(status: "reauth_required")), "Reconnect needed — sign-in expired")
        XCTAssertEqual(mcpServerStatusLabel(server(enabled: false)), "Disabled")
        XCTAssertEqual(mcpServerStatusLabel(server(toolCount: 0, lastSyncedAt: nil)), "Pending discovery")
        XCTAssertEqual(mcpServerStatusLabel(server(toolCount: 0, lastSyncedAt: "2026-08-25T13:00:00Z")), "No tools discovered")
        XCTAssertEqual(mcpServerStatusLabel(server(toolCount: 1)), "Connected — 1 tool")
        XCTAssertEqual(mcpServerStatusLabel(server(toolCount: 3)), "Connected — 3 tools")
    }

    func testMcpServerHostExtractsTheHostname() {
        XCTAssertEqual(mcpServerHost("https://example.com/mcp"), "example.com")
        // Unparseable as a URL — echoed verbatim rather than dropped.
        XCTAssertEqual(mcpServerHost("not a url"), "not a url")
    }
}

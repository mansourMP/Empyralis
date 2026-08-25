import XCTest
@testable import Empyralis

/// THE FIXTURE IS CAPTURED FROM THE PRODUCER, NOT WRITTEN FROM THE MODEL.
///
/// This is a regression test for a defect that shipped and survived every
/// existing test: `Agent` decoded `id` from a `"id"` key that
/// `fleet_list_agents` does not emit — it emits `agent_id`. Every row threw
/// `keyNotFound`, which took the whole `AgentsResponse` down;
/// `WorkspaceStore.refresh` wraps that fetch in `try?`, so the throw became
/// an empty array and the Agents tab rendered "No agents yet" at a
/// workspace that had agents.
///
/// A hand-written fixture cannot catch this, because whoever writes it
/// writes the keys the model already expects. The JSON below is a verbatim
/// capture of `GET /api/w/{ws}/fleet/agents` against a real backend
/// (2026-08-25), trimmed of nothing that matters and reformatted only for
/// line width.
final class AgentDecodingTests: XCTestCase {

    /// Verbatim from the live route. Two specialists and the master install.
    private let liveAgentsPayload = """
    {
      "ok": true,
      "agents": [
        {
          "agent_id": "ainstall_0bbc7124d3f84e34",
          "label": "Release Scout",
          "role": "specialist",
          "purpose_preset": "internal_assistant",
          "audience": "owner",
          "project_id": "project_3de4d995f9844aa7",
          "agent_kind": "specialist",
          "status": "active",
          "enabled": true,
          "subagents_enabled": false,
          "model_config": {"mode": "platform_credits", "model": "deepseek-v4-pro"},
          "capability_preset": "standard",
          "hardware_access": "none",
          "hardware_access_locked": false,
          "context_policy": {"on_context_full": "compact", "max_context_tokens": 0},
          "instructions": "Runs the iOS release checklist.",
          "skills": [],
          "preferred_gateway_id": "",
          "telegram_first_contact_reply": false,
          "stopped": {"active": false},
          "runtime_target": "cloud",
          "hardware_status": "online",
          "hardware_status_reason": null,
          "last_heartbeat": null,
          "current_run_id": null,
          "last_activity": "2026-08-25T08:17:40.392347+00:00",
          "activity_preview": "Created",
          "channel": ""
        },
        {
          "agent_id": "ainstall_2e36d29673814286",
          "label": "Billing Watcher",
          "agent_kind": "specialist",
          "status": "active",
          "enabled": true,
          "stopped": {"active": false},
          "current_run_id": null,
          "channel": ""
        },
        {
          "agent_id": "ainstall_ws-35d35c8165e6_sage",
          "label": "Sage",
          "agent_kind": "master",
          "status": "active",
          "enabled": true,
          "stopped": {"active": false},
          "current_run_id": null,
          "channel": ""
        }
      ]
    }
    """.data(using: .utf8)!

    func testDecodesTheRealRouteResponse() throws {
        let decoded = try JSONDecoder().decode(AgentsResponse.self, from: liveAgentsPayload)
        XCTAssertTrue(decoded.ok)
        XCTAssertEqual(decoded.agents.count, 3, "a single bad key takes the WHOLE list down, not one row")
    }

    /// The specific mistake: the identifier must come off `agent_id`. If it
    /// ever silently falls back to something else, `displayName` and every
    /// route built from the id go with it.
    func testIdentifierComesFromAgentId() throws {
        let decoded = try JSONDecoder().decode(AgentsResponse.self, from: liveAgentsPayload)
        XCTAssertEqual(decoded.agents.first?.id, "ainstall_0bbc7124d3f84e34")
        XCTAssertEqual(decoded.agents.first?.label, "Release Scout")
        XCTAssertEqual(decoded.agents.first?.displayName, "Release Scout")
    }

    /// The route sends NO `id` key. Proving its absence is the whole point —
    /// a test written against a payload that happens to carry both keys
    /// would pass under the original bug.
    func testTheRouteSendsNoPlainIdKey() throws {
        let raw = try XCTUnwrap(
            JSONSerialization.jsonObject(with: liveAgentsPayload) as? [String: Any]
        )
        let rows = try XCTUnwrap(raw["agents"] as? [[String: Any]])
        for row in rows {
            XCTAssertNil(row["id"], "the capture must keep the route's real shape")
            XCTAssertNotNil(row["agent_id"])
        }
    }

    func testStatusVocabularyIsHonest() throws {
        let decoded = try JSONDecoder().decode(AgentsResponse.self, from: liveAgentsPayload)
        // Nothing in the capture is running or stopped, so nothing may claim
        // to be. "Ready" is the honest floor.
        XCTAssertEqual(decoded.agents.map(\.statusLabel), ["Ready", "Ready", "Ready"])

        let stopped = try JSONDecoder().decode(Agent.self, from: """
        {"agent_id": "a1", "label": "X", "stopped": {"active": true}}
        """.data(using: .utf8)!)
        XCTAssertEqual(stopped.statusLabel, "Stopped")

        let working = try JSONDecoder().decode(Agent.self, from: """
        {"agent_id": "a2", "label": "Y", "current_run_id": "run_1"}
        """.data(using: .utf8)!)
        XCTAssertEqual(working.statusLabel, "Working")
    }
}

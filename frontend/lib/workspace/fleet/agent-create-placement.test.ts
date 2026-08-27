/**
 * Drives the REAL placement rules from agent-create-placement.ts.
 * Run: npx tsx lib/workspace/fleet/agent-create-placement.test.ts
 */

import {
  AGENT_CREATE_DEFAULT_PLACEMENT,
  AGENT_CREATE_PLACEMENTS,
  dedupeHardwareNodeLabels,
  hardwareNodeId,
  hardwareNodeLabel,
  hardwareNodeOnline,
  nodesForPlacement,
  partitionHardwareNodes,
  placementNeedsNode,
  planAgentCreatePlacement,
  type AgentCreatePlacement,
  type HardwareNodeLike,
} from "./agent-create-placement";

let passed = 0;
let failed = 0;

function assert(condition: boolean, label: string): void {
  if (condition) passed++;
  else {
    failed++;
    console.error(`FAIL: ${label}`);
  }
}

const ALL: AgentCreatePlacement[] = AGENT_CREATE_PLACEMENTS.map((p) => p.id);

// ── The three buckets ─────────────────────────────────────────────────────

assert(ALL.join(">") === "cloud>vps>gateway", "the same three buckets the Hardware tab uses, cloud first");
assert(AGENT_CREATE_DEFAULT_PLACEMENT === "cloud", "cloud only is the default — it needs nothing");
assert(
  AGENT_CREATE_PLACEMENTS.every((p) => p.label.trim() && p.body.trim()),
  "every option states what it is and what it costs, on its own face",
);
assert(
  AGENT_CREATE_PLACEMENTS.every((p) => p.body.split(/[.!?]\s/).filter((s) => s.trim()).length <= 2),
  "at most two short sentences per option — a professional tool labels, it does not lecture",
);
assert(
  !AGENT_CREATE_PLACEMENTS.some((p) => /project/i.test(p.label) || /project/i.test(p.body)),
  "placement answers WHICH MACHINE, never which project — an agent belongs to the workspace",
);
assert(
  !AGENT_CREATE_PLACEMENTS.some((p) => /gateway|docker|npm|binary|daemon/i.test(p.body)),
  "no mechanism in customer prose — the founder's standing instruction on setup copy",
);

assert(!placementNeedsNode("cloud"), "cloud needs no machine");
assert(placementNeedsNode("vps") && placementNeedsNode("gateway"), "both machine placements need one named");

// ── Node reading is defensive, because it is a wire payload ───────────────

{
  assert(hardwareNodeId({ gateway_id: " gw_1 " }) === "gw_1", "gateway_id wins and is trimmed");
  assert(hardwareNodeId({ id: "gw_2" }) === "gw_2", "…and `id` is the fallback the same endpoint also uses");
  assert(hardwareNodeId({}) === "", "an unidentifiable node resolves to empty, never to undefined");

  // display_name FIRST — it is the real, gateway-reported hostname
  // (empyralis-gateway/src/config.ts's displayName, os.hostname() by
  // default). hardware_label is NOT an owner-chosen name: it is
  // server-computed from hardware_kind alone, and for every non-VPS
  // ("personal_device") registration it is the identical literal constant
  // "This Device" — so checking it first is what made two real, distinctly
  // named paired computers both render as "This Device" in the wizard.
  assert(hardwareNodeLabel({ display_name: "box-a", hardware_label: "This Device" }) === "box-a", "display_name beats the generic hardware_label fallback");
  assert(hardwareNodeLabel({ hardware_label: "DigitalOcean · SFO3" }) === "DigitalOcean · SFO3", "…falls back to hardware_label when there is no real name");
  assert(hardwareNodeLabel({ platform: "darwin" }) === "darwin", "…then the platform");
  assert(hardwareNodeLabel({ gateway_id: "gw_9" }) === "gw_9", "…then the raw id");
  assert(hardwareNodeLabel({}) === "Computer", "and a node with nothing at all still renders a word");

  assert(hardwareNodeOnline({ connection_status: "ONLINE" }), "online is matched case-insensitively");
  assert(hardwareNodeOnline({ status: "online" }), "either field can carry it");
  assert(!hardwareNodeOnline({ status: "offline" }), "and offline is not online");
  assert(!hardwareNodeOnline({}), "an unknown node is not reported as online");
}

// ── Two entries may never render the same label — the founder's own report ─

{
  // The exact reported scenario: two paired personal computers, the
  // backend's generic "This Device" hardware_label on both, real distinct
  // hostnames on both — must resolve to those hostnames, not collide.
  const macbook: HardwareNodeLike = { gateway_id: "gw_1", display_name: "Mansurs-MacBook-Pro.local", hardware_label: "This Device", connection_status: "online" };
  const vps: HardwareNodeLike = { gateway_id: "gw_2", display_name: "ubuntu-s-1vcpu-2gb-sfo2", hardware_label: "This Device", connection_status: "offline" };
  const labels = dedupeHardwareNodeLabels([macbook, vps]);
  assert(labels.get("gw_1") === "Mansurs-MacBook-Pro.local", "the online machine keeps its real hostname");
  assert(labels.get("gw_2") === "ubuntu-s-1vcpu-2gb-sfo2", "the offline machine keeps its real hostname too");
  assert(labels.get("gw_1") !== labels.get("gw_2"), "the two are never the same string");

  // Two nodes that genuinely have no real name at all — both fall to the
  // shared "This Device" fallback and MUST still be disambiguated, never
  // shown identically.
  const bare1: HardwareNodeLike = { gateway_id: "gw_a", hardware_label: "This Device", platform: "darwin" };
  const bare2: HardwareNodeLike = { gateway_id: "gw_bcdef0", hardware_label: "This Device", platform: "linux" };
  const bareLabels = dedupeHardwareNodeLabels([bare1, bare2]);
  assert(bareLabels.get("gw_a") !== bareLabels.get("gw_bcdef0"), "two nameless nodes never collide either");
  assert(bareLabels.get("gw_a") === "This Device (darwin)", "…disambiguated by platform when it actually differs");
  assert(bareLabels.get("gw_bcdef0") === "This Device (linux)", "…both sides get the same treatment");
  assert(!/\(\d+\)$/.test(bareLabels.get("gw_a") || ""), "never a bare ordinal — an order that means nothing");

  // Same platform on both — the platform can't help, so a short id slice
  // (a real, stable, already-server-assigned fact) is the disambiguator.
  const same1: HardwareNodeLike = { gateway_id: "gw_1a2b3c", hardware_label: "This Device", platform: "linux" };
  const same2: HardwareNodeLike = { gateway_id: "gw_4d5e6f", hardware_label: "This Device", platform: "linux" };
  const sameLabels = dedupeHardwareNodeLabels([same1, same2]);
  assert(sameLabels.get("gw_1a2b3c") === "This Device · 1a2b3c", "falls to an id slice when platform can't distinguish");
  assert(sameLabels.get("gw_4d5e6f") === "This Device · 4d5e6f", "…and the slice is the node's own real id, not invented");
  assert(sameLabels.get("gw_1a2b3c") !== sameLabels.get("gw_4d5e6f"), "still never the same string");

  // A single node, or already-distinct labels, are left completely alone.
  const solo = dedupeHardwareNodeLabels([{ gateway_id: "gw_1", display_name: "Only Box" }]);
  assert(solo.get("gw_1") === "Only Box", "one node is never disambiguated for no reason");
  const distinct = dedupeHardwareNodeLabels([
    { gateway_id: "gw_1", display_name: "Mac mini" },
    { gateway_id: "gw_2", display_name: "Ubuntu box" },
  ]);
  assert(distinct.get("gw_1") === "Mac mini" && distinct.get("gw_2") === "Ubuntu box", "already-distinct real names pass through untouched");

  assert(dedupeHardwareNodeLabels([]).size === 0, "an empty list disambiguates cleanly");
}

// ── Partitioning ──────────────────────────────────────────────────────────

{
  const nodes = [
    { gateway_id: "v1", hardware_kind: "cloud_vps" },
    { gateway_id: "g1", hardware_kind: "personal_computer" },
    { gateway_id: "g2" },
  ];
  const split = partitionHardwareNodes(nodes);
  assert(split.vps.map(hardwareNodeId).join(",") === "v1", "cloud_vps is the ONLY discriminator for the VPS side");
  assert(
    split.gateway.map(hardwareNodeId).join(",") === "g1,g2",
    "an unknown/absent kind counts as a paired computer — a node nobody can select is one the customer cannot use",
  );
  assert(nodesForPlacement("cloud", nodes).length === 0, "cloud picks from neither partition");
  assert(nodesForPlacement("vps", nodes).length === 1, "vps picks from the vps partition");
  assert(nodesForPlacement("gateway", nodes).length === 2, "gateway picks from the rest");
  assert(partitionHardwareNodes([]).vps.length === 0, "an empty list partitions cleanly");
}

// ── The plan ──────────────────────────────────────────────────────────────

{
  const cloud = planAgentCreatePlacement({
    placement: "cloud",
    nodeId: "",
    nodesKnown: false,
    availableNodeCount: 0,
  });
  assert(cloud.ready, "cloud is always ready — there is nothing to pick");
  assert(cloud.blockedReason === "", "and nothing to explain");
  assert(
    cloud.patch === null,
    "cloud writes NOTHING: the server's own standard preset already resolves hardware_access to none",
  );
}

{
  const looking = planAgentCreatePlacement({
    placement: "gateway",
    nodeId: "",
    nodesKnown: false,
    availableNodeCount: 0,
  });
  assert(!looking.ready, "a machine placement with no machine named is not ready");
  assert(
    looking.blockedReason === "",
    "and it says NOTHING while the list is still loading — 'you have none' and 'I have not looked' are different facts",
  );
}

{
  const empty = planAgentCreatePlacement({
    placement: "gateway",
    nodeId: "",
    nodesKnown: true,
    availableNodeCount: 0,
  });
  assert(empty.blockedReason === "No computers paired yet.", "zero machines is stated as zero machines");
  const emptyVps = planAgentCreatePlacement({
    placement: "vps",
    nodeId: "",
    nodesKnown: true,
    availableNodeCount: 0,
  });
  assert(
    emptyVps.blockedReason === "No cloud servers connected yet.",
    "and the two kinds are named separately — 'server' and 'computer' are what the customer is looking at",
  );

  const unpicked = planAgentCreatePlacement({
    placement: "gateway",
    nodeId: "",
    nodesKnown: true,
    availableNodeCount: 2,
  });
  assert(
    unpicked.blockedReason !== empty.blockedReason && /pick/i.test(unpicked.blockedReason),
    "having machines and not choosing one is a DIFFERENT fact from having none, and reads as one",
  );
}

{
  const picked = planAgentCreatePlacement({
    placement: "gateway",
    nodeId: " gw_7 ",
    nodesKnown: true,
    availableNodeCount: 1,
  });
  assert(picked.ready && picked.blockedReason === "", "a named machine is ready");
  assert(
    picked.patch?.hardware_access === "gateway" && picked.patch?.preferred_gateway_id === "gw_7",
    "both columns are written TOGETHER, and the id is trimmed",
  );

  const vps = planAgentCreatePlacement({
    placement: "vps",
    nodeId: "v1",
    nodesKnown: true,
    availableNodeCount: 1,
  });
  assert(vps.patch?.hardware_access === "vps", "the bucket matches the placement rather than being hardcoded");
}

// A non-cloud placement may NEVER emit half of itself: an access bucket with
// no box is an agent that believes it has hardware and can reach none.
{
  for (const placement of ALL) {
    for (const nodeId of ["", "  ", "gw_1"]) {
      const plan = planAgentCreatePlacement({
        placement,
        nodeId,
        nodesKnown: true,
        availableNodeCount: 1,
      });
      if (!plan.patch) continue;
      assert(
        plan.patch.hardware_access === "cloud"
          ? false
          : Boolean(plan.patch.preferred_gateway_id.trim()),
        `placement "${placement}" never emits a machine bucket with an empty box id`,
      );
      assert(plan.ready, `an emitted patch is only ever produced by a READY plan ("${placement}")`);
    }
  }
}

// ── Summary ───────────────────────────────────────────────────────────────

console.log(`${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);

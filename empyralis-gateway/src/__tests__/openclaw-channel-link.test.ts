/**
 * Linking a channel that has nothing to paste.
 *
 * The two things worth pinning here are both about NOT guessing:
 *
 *   1. "Can this channel be linked by scanning?" is answered by the plugin's
 *      own declared gateway methods, never by a channel id. The fixtures below
 *      are OpenClaw's real output shape, captured from the live rig against
 *      openclaw@2026.6.10:
 *
 *        $ openclaw --profile empyralis channels capabilities --channel all --json
 *        telegram | gatewayMethodDescriptors: []
 *        whatsapp | gatewayMethodDescriptors: ['web.login.start','web.login.wait']
 *
 *   2. An outcome carries THREE separable facts — linked / a code to show /
 *      neither — and the projection must never collapse two of them. That is
 *      what keeps a panel from showing a stale square or claiming a success it
 *      cannot see.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  deriveLinkShape,
  parseChannelCapabilities,
  projectLinkResponse,
} from "../openclaw/provisioning/openclaw-channel-link";

test("a plugin declaring both QR methods is QR-linkable; one of the two is not enough", () => {
  assert.equal(deriveLinkShape(["web.login.start", "web.login.wait"]).supports_qr_login, true);
  // A half-declared seam cannot complete a link: `start` with no `wait` can
  // produce a code and never tell anyone it was scanned. Treating that as
  // "supports QR" would render a control that strands the customer.
  assert.equal(deriveLinkShape(["web.login.start"]).supports_qr_login, false);
  assert.equal(deriveLinkShape(["web.login.wait"]).supports_qr_login, false);
  assert.equal(deriveLinkShape([]).supports_qr_login, false);
  // Unrelated methods never imply a login seam.
  assert.equal(deriveLinkShape(["channels.status", "message.action"]).supports_qr_login, false);
});

test("capabilities output is parsed into per-channel link shapes, and nothing names a channel", () => {
  const shapes = parseChannelCapabilities(
    JSON.stringify({
      channels: [
        { plugin: { id: "telegram", gatewayMethodDescriptors: [] } },
        {
          plugin: {
            id: "whatsapp",
            gatewayMethodDescriptors: [{ name: "web.login.start" }, { name: "web.login.wait" }],
          },
        },
      ],
    }),
  );
  assert.equal(shapes.telegram.supports_qr_login, false);
  assert.equal(shapes.whatsapp.supports_qr_login, true);
  assert.deepEqual(shapes.whatsapp.gateway_methods, ["web.login.start", "web.login.wait"]);
  // A channel OpenClaw did not report on has no entry at all. That is a third
  // value, distinct from `supports_qr_login: false`, and the caller reports it
  // as "we do not know" rather than as "it has no QR".
  assert.equal(shapes.signal, undefined);
});

test("unparsable or unexpected capabilities output yields no shapes rather than throwing", () => {
  // A read that fails must degrade to "we do not know how this links", never
  // take down a channel list that otherwise succeeded.
  assert.deepEqual(parseChannelCapabilities("not json"), {});
  assert.deepEqual(parseChannelCapabilities(JSON.stringify({ channels: "nope" })), {});
  assert.deepEqual(parseChannelCapabilities(JSON.stringify({})), {});
  assert.deepEqual(parseChannelCapabilities(JSON.stringify({ channels: [{ plugin: {} }] })), {});
});

test("a code, a completed link and a refusal are three different outcomes", () => {
  const code = "data:image/png;base64,iVBORw0KGgo=";

  const showing = projectLinkResponse({ ok: true, result: { connected: false, qr_data_url: code, message: "Scan this." } });
  assert.equal(showing.status, "ok");
  assert.equal(showing.linked, false);
  assert.equal(showing.qr_data_url, code);
  assert.equal(showing.message, "Scan this.");

  const linked = projectLinkResponse({ ok: true, result: { connected: true, message: "Linked." } });
  assert.equal(linked.linked, true);
  assert.equal(linked.qr_data_url, null);

  // Accepted, but produced nothing. NOT a refusal — the box answered — and not
  // a code either. A caller that collapsed this into either one would show a
  // spinner forever or a broken image.
  const nothing = projectLinkResponse({ ok: true, result: { connected: false, message: "" } });
  assert.equal(nothing.status, "ok");
  assert.equal(nothing.linked, false);
  assert.equal(nothing.qr_data_url, null);

  const refused = projectLinkResponse({ ok: false, error: { code: "UNAVAILABLE", message: "no provider" } });
  assert.equal(refused.status, "refused");
  assert.equal(refused.refusal?.code, "UNAVAILABLE");
  assert.equal(refused.linked, false);
  assert.equal(refused.qr_data_url, null);
});

test("only a real image data URL is ever handed on as a code", () => {
  // The panel puts this straight into an <img src>. A non-image value reaching
  // that attribute is both a broken control and an injection surface, so the
  // filter is here rather than in the component.
  for (const bad of ["javascript:alert(1)", "https://example.test/qr.png", "data:text/html,<script>", ""]) {
    const outcome = projectLinkResponse({ ok: true, result: { connected: false, qr_data_url: bad } });
    assert.equal(outcome.qr_data_url, null, `${bad || "(empty)"} is not accepted as a code`);
  }
});

test("a refusal with no code still carries a stable code, never invented prose", () => {
  const refused = projectLinkResponse({ ok: false });
  assert.equal(refused.status, "refused");
  assert.equal(refused.refusal?.code, "openclaw_channel_link_failed");
  assert.ok((refused.refusal?.detail ?? "").length > 0);
});

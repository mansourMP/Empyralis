import test from "node:test";
import assert from "node:assert/strict";

import { mapWhatsAppInboundMessage } from "../channels/whatsapp/message-mapper";

// FIX (group-gate bypass): message-mapper.ts's only "is this a conversation
// I can gate/reply to" branch was `remoteJid.endsWith("@g.us")` (isGroup).
// WhatsApp Status updates (the fixed "status@broadcast" JID) and
// Channels/newsletters ("@newsletter" JIDs) don't end in "@g.us", so they
// fell through with is_group=false AND is_self_chat=false -- landing in
// runtime.ts's handleMessagesUpsert exactly like an ungated 1:1 DM, which
// auto-replies unconditionally. A status update or a channel/newsletter
// post is a one-to-many broadcast, not a conversation with a person on the
// other end of remoteJid -- there is nothing to reply to. Fixed by hard-
// dropping both at the mapper level (mapWhatsAppInboundMessage returns
// null), before any group/self-chat classification runs, so nothing
// downstream ever sees them.
//
// These are pure mapper-level tests (mapWhatsAppInboundMessage only) --
// runtime.ts's handleMessagesUpsert/group gate are covered separately in
// whatsapp-self-chat.test.ts / telegram-group-gate.test.ts's WhatsApp
// counterpart pattern; this fix's file scope is message-mapper.ts alone.

function rawTextMessage(remoteJid: string, text: string, id = "wamid-fix4") {
  return {
    key: { id, remoteJid, fromMe: false },
    message: { conversation: text },
    messageTimestamp: Math.floor(Date.now() / 1000),
    pushName: "Some Contact",
  };
}

test("mapWhatsAppInboundMessage: a status@broadcast inbound is hard-dropped -- never treated as a DM", () => {
  const raw = rawTextMessage("status@broadcast", "My battery is at 1% \u{1F62D}");
  const mapped = mapWhatsAppInboundMessage(raw, { ownedJid: "15550001111@s.whatsapp.net" });
  assert.equal(mapped, null, "a status update must never be published as an inbound message at all");
});

test("mapWhatsAppInboundMessage: status@broadcast is dropped even with media and no ownedJid known", () => {
  const raw = {
    key: { id: "wamid-status-media", remoteJid: "status@broadcast", fromMe: false },
    message: { imageMessage: { mimetype: "image/jpeg" } },
    messageTimestamp: Math.floor(Date.now() / 1000),
  };
  const mediaItem = {
    kind: "image" as const,
    media_id: "media-status-1",
    mime_type: "image/jpeg",
    size_bytes: 999,
  };
  // No ownedJid supplied at all (e.g. not yet resolved) -- must still drop.
  const mapped = mapWhatsAppInboundMessage(raw, { media: [mediaItem] });
  assert.equal(mapped, null, "a status update must be dropped regardless of media or ownedJid availability");
});

test("mapWhatsAppInboundMessage: a @newsletter (Channels) post is hard-dropped -- never treated as a DM", () => {
  const raw = rawTextMessage("120363012345678901@newsletter", "New episode is live!");
  const mapped = mapWhatsAppInboundMessage(raw, { ownedJid: "15550001111@s.whatsapp.net" });
  assert.equal(mapped, null, "a channel/newsletter post must never be published as an inbound message at all");
});

test("mapWhatsAppInboundMessage: an ordinary 1:1 DM is unaffected -- still maps normally", () => {
  const raw = rawTextMessage("15551234567@s.whatsapp.net", "hey, are you there?");
  const mapped = mapWhatsAppInboundMessage(raw, { ownedJid: "15550001111@s.whatsapp.net" });
  assert.ok(mapped, "an ordinary 1:1 DM must still be mapped");
  assert.equal(mapped!.message.is_group, false);
  assert.equal(mapped!.message.is_self_chat, false);
  assert.equal(mapped!.message.text, "hey, are you there?");
});

test("mapWhatsAppInboundMessage: an ordinary @g.us group message is unaffected -- the group gate still applies", () => {
  // Do NOT touch the ordinary-group path: a real WhatsApp group (including
  // a Community's own announcement group, which Baileys also represents as
  // an "@g.us" JID -- see message-mapper.ts's isGroup comment) must keep
  // is_group=true exactly as before this fix.
  const raw = rawTextMessage("120363000000000000@g.us", "what time is dinner");
  const mapped = mapWhatsAppInboundMessage(raw, { ownedJid: "15550001111@s.whatsapp.net" });
  assert.ok(mapped, "an ordinary group message must still be mapped (the group gate runs downstream in runtime.ts)");
  assert.equal(mapped!.message.is_group, true);
});

test("mapWhatsAppInboundMessage: is_self_chat is unaffected by the broadcast/newsletter check", () => {
  const ownedJid = "15550001111@s.whatsapp.net";
  const raw = rawTextMessage(ownedJid, "remind me to call mom");
  const mapped = mapWhatsAppInboundMessage(raw, { ownedJid });
  assert.ok(mapped, "self-chat (Saved Messages) must still be mapped");
  assert.equal(mapped!.message.is_self_chat, true);
  assert.equal(mapped!.message.is_group, false);
});

import assert from "node:assert/strict";
import test from "node:test";

import { buildWeChatTextReplyXml, parseWeChatXml } from "../channels/wechat/xml";

test("parseWeChatXml extracts CDATA-wrapped and plain leaf tags", () => {
  const xml = `<xml>
  <ToUserName><![CDATA[toUser]]></ToUserName>
  <FromUserName><![CDATA[fromUser]]></FromUserName>
  <CreateTime>1348831860</CreateTime>
  <MsgType><![CDATA[text]]></MsgType>
  <Content><![CDATA[this is a test]]></Content>
  <MsgId>1234567890123456</MsgId>
</xml>`;
  const fields = parseWeChatXml(xml);
  assert.equal(fields.ToUserName, "toUser");
  assert.equal(fields.FromUserName, "fromUser");
  assert.equal(fields.CreateTime, "1348831860");
  assert.equal(fields.MsgType, "text");
  assert.equal(fields.Content, "this is a test");
  assert.equal(fields.MsgId, "1234567890123456");
});

test("parseWeChatXml handles content containing angle-bracket-free special characters inside CDATA", () => {
  const xml = `<xml><Content><![CDATA[hello & <world> "quoted"]]></Content></xml>`;
  const fields = parseWeChatXml(xml);
  assert.equal(fields.Content, 'hello & <world> "quoted"');
});

test("parseWeChatXml returns an empty object for empty or non-XML input", () => {
  assert.deepEqual(parseWeChatXml(""), {});
  assert.deepEqual(parseWeChatXml("not xml at all"), {});
});

test("buildWeChatTextReplyXml leaves ordinary content untouched inside CDATA (no entity-escaping needed there)", () => {
  const xml = buildWeChatTextReplyXml({
    toUserName: "fromUser",
    fromUserName: "toUser",
    content: "5 < 10 & 10 > 5",
    createTime: 123,
  });
  assert.match(xml, /<Content><!\[CDATA\[5 < 10 & 10 > 5\]\]><\/Content>/);
  assert.match(xml, /<CreateTime>123<\/CreateTime>/);
  assert.match(xml, /<MsgType><!\[CDATA\[text\]\]><\/MsgType>/);

  // Round-trips back through the parser.
  const fields = parseWeChatXml(xml);
  assert.equal(fields.Content, "5 < 10 & 10 > 5");
  assert.equal(fields.ToUserName, "fromUser");
});

test("buildWeChatTextReplyXml neutralizes a literal ']]>' using the standard split-CDATA-section technique", () => {
  const xml = buildWeChatTextReplyXml({
    toUserName: "fromUser",
    fromUserName: "toUser",
    content: "watch out for ]]> in here",
    createTime: 123,
  });
  // The naive single-CDATA-block reader in this file (parseWeChatXml) is
  // documented to handle WeChat's real inbound shape (one CDATA section
  // per tag), not the split-section escaping technique used here for the
  // rare literal-"]]>"-in-content case — a real XML parser reconstructs
  // adjacent CDATA sections as one continuous text run per the XML spec,
  // which is what's asserted here structurally instead of via round-trip.
  assert.match(xml, /<Content><!\[CDATA\[watch out for \]\]\]\]><!\[CDATA\[> in here\]\]><\/Content>/);
  // Splitting this way must produce well-formed adjacent CDATA sections:
  // no bare "]]>" appears except at legitimate section boundaries.
  const contentMatch = /<Content>([\s\S]*)<\/Content>/.exec(xml);
  assert.ok(contentMatch);
  assert.equal(contentMatch![1], "<![CDATA[watch out for ]]]]><![CDATA[> in here]]>");
});

/** Minimal parser/serializer for WeChat's/WeCom's inbound message XML.
 *
 *  WeChat's callback body is always a single flat `<xml>` element whose
 *  children are simple leaf tags, each either a bare text node or a
 *  `<![CDATA[...]]>`-wrapped one — never nested elements, attributes, or
 *  external entities:
 *
 *    <xml>
 *      <ToUserName><![CDATA[toUser]]></ToUserName>
 *      <FromUserName><![CDATA[fromUser]]></FromUserName>
 *      <CreateTime>1348831860</CreateTime>
 *      <MsgType><![CDATA[text]]></MsgType>
 *      <Content><![CDATA[this is a test]]></Content>
 *      <MsgId>1234567890123456</MsgId>
 *    </xml>
 *
 *  Deliberately NOT using a general-purpose XML/DOM parser here: this
 *  fixed, flat, attribute-free shape doesn't need one, and skipping one
 *  sidesteps XXE / entity-expansion classes of vulnerability entirely for
 *  a body that arrives unauthenticated-by-transport (signature
 *  verification in signature.ts happens on the query string, not the
 *  body — the POST body itself is trusted only after that check, but
 *  parsing should still not be a landmine on its own). If WeChat's
 *  "safe mode" (encrypted body) is added later, the decrypted plaintext
 *  is this same flat shape, so this parser still applies.
 */
import type { WeChatInboundXmlFields } from "./types";

const LEAF_TAG_PATTERN = /<([A-Za-z_][\w.-]*)>(?:<!\[CDATA\[([\s\S]*?)\]\]>|([^<]*))<\/\1>/g;

/** Parses a flat WeChat/WeCom XML callback body into a plain string map.
 *  Returns an empty object for empty/non-XML input rather than throwing —
 *  callers (server.ts) decide how to respond to a body that didn't parse
 *  into anything usable. */
export function parseWeChatXml(raw: string): WeChatInboundXmlFields {
  const input = String(raw || "");
  const fields: WeChatInboundXmlFields = {};
  let match: RegExpExecArray | null;
  LEAF_TAG_PATTERN.lastIndex = 0;
  while ((match = LEAF_TAG_PATTERN.exec(input)) !== null) {
    const [, tag, cdataValue, plainValue] = match;
    const value = cdataValue !== undefined ? cdataValue : (plainValue ?? "");
    fields[tag] = value.trim();
  }
  return fields;
}

/** All of WeChat's/WeCom's text-value leaf tags (ToUserName, FromUserName,
 *  MsgType, Content, ...) are CDATA-wrapped, so `<`/`>`/`&` inside the
 *  value need no entity-escaping — CDATA content is raw text. The one
 *  thing that DOES need neutralizing is a literal `]]>` inside the value,
 *  since that's CDATA's own terminator; the standard trick is to close
 *  the section early, emit a literal `]]>`, then reopen a fresh CDATA
 *  section for the remainder. */
function sanitizeForCdata(value: string): string {
  return String(value || "").split("]]>").join("]]]]><![CDATA[>");
}

/** Builds a synchronous plaintext-reply XML body (the alternative to the
 *  async customer-service send API in outbound.ts — WeChat lets a
 *  callback handler reply within ~5s directly in the HTTP response body
 *  instead of making a second authenticated call). Not used by server.ts
 *  today (it always ACKs with "success" and sends async via outbound.ts
 *  for a uniform code path across both official-account variants and
 *  media types), but kept here since it's a one-line addition callers
 *  may want later and the escaping is easy to get wrong ad hoc. */
export function buildWeChatTextReplyXml(params: {
  toUserName: string;
  fromUserName: string;
  content: string;
  createTime?: number;
}): string {
  const createTime = params.createTime ?? Math.floor(Date.now() / 1000);
  return [
    "<xml>",
    `<ToUserName><![CDATA[${sanitizeForCdata(params.toUserName)}]]></ToUserName>`,
    `<FromUserName><![CDATA[${sanitizeForCdata(params.fromUserName)}]]></FromUserName>`,
    `<CreateTime>${createTime}</CreateTime>`,
    `<MsgType><![CDATA[text]]></MsgType>`,
    `<Content><![CDATA[${sanitizeForCdata(params.content)}]]></Content>`,
    "</xml>",
  ].join("");
}

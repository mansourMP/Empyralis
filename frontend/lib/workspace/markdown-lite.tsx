import type { ReactNode } from "react";

/**
 * Shared "markdown-lite" renderer for chat turns — bold/italic/code/links and
 * simple lists, enough that agent replies with real formatting don't show up
 * as literal asterisks. Not a full markdown document renderer (no new
 * dependency for what's still a chat bubble, not a doc viewer):
 * headings/tables/blockquotes are deliberately out of scope.
 *
 * Originally local to the Work tab's transcript view; the live agent chat
 * pipeline (chat-message.tsx) rendered raw text with no parsing at all, so
 * "**bold**" showed up literally there. Both surfaces now import this same
 * module rather than keeping two copies (or one copy and one gap).
 */

export function renderMarkdownLiteInline(text: string, keyPrefix: string): ReactNode[] {
  const pattern = /`([^`]+)`|\[([^\]]+)\]\(([^)]+)\)|\*\*([^*]+)\*\*|\*([^*]+)\*|_([^_]+)_/g;
  const nodes: ReactNode[] = [];
  let last = 0;
  let i = 0;
  let m: RegExpExecArray | null;
  while ((m = pattern.exec(text))) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    if (m[1] !== undefined) {
      nodes.push(<code key={`${keyPrefix}-${i++}`} className="fleet-md-code">{m[1]}</code>);
    } else if (m[2] !== undefined) {
      nodes.push(
        <a key={`${keyPrefix}-${i++}`} href={m[3]} target="_blank" rel="noreferrer" className="fleet-link">
          {m[2]}
        </a>,
      );
    } else if (m[4] !== undefined) {
      nodes.push(<strong key={`${keyPrefix}-${i++}`}>{m[4]}</strong>);
    } else if (m[5] !== undefined) {
      nodes.push(<em key={`${keyPrefix}-${i++}`}>{m[5]}</em>);
    } else if (m[6] !== undefined) {
      nodes.push(<em key={`${keyPrefix}-${i++}`}>{m[6]}</em>);
    }
    last = pattern.lastIndex;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

export type MarkdownLiteBlock = { type: "p" | "ul" | "ol"; text?: string; items?: string[] };

export function parseMarkdownLiteBlocks(text: string): MarkdownLiteBlock[] {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: MarkdownLiteBlock[] = [];
  let para: string[] = [];
  let list: string[] = [];
  let listType: "ul" | "ol" | null = null;

  const flushPara = () => {
    if (para.length) blocks.push({ type: "p", text: para.join("\n") });
    para = [];
  };
  const flushList = () => {
    if (listType && list.length) blocks.push({ type: listType, items: list });
    list = [];
    listType = null;
  };

  for (const line of lines) {
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+\.\s+(.*)$/.exec(line);
    if (bullet) {
      flushPara();
      if (listType !== "ul") { flushList(); listType = "ul"; }
      list.push(bullet[1]);
    } else if (numbered) {
      flushPara();
      if (listType !== "ol") { flushList(); listType = "ol"; }
      list.push(numbered[1]);
    } else if (line.trim() === "") {
      flushPara();
      flushList();
    } else {
      flushList();
      para.push(line);
    }
  }
  flushPara();
  flushList();
  return blocks;
}

/** Renders a chat turn's plain-text content as markdown-lite blocks. Renders
 *  nothing for empty text — callers that need a non-empty fallback (e.g. a
 *  placeholder for a still-streaming turn) handle that themselves. */
export function MarkdownLiteText({ text }: { text: string }) {
  const blocks = parseMarkdownLiteBlocks(text);
  if (blocks.length === 0) return null;
  return (
    <>
      {blocks.map((b, bi) => {
        if (b.type === "ul" || b.type === "ol") {
          const ListTag = b.type;
          return (
            <ListTag key={bi} className="fleet-md-list">
              {(b.items || []).map((item, ii) => (
                <li key={ii}>{renderMarkdownLiteInline(item, `${bi}-${ii}`)}</li>
              ))}
            </ListTag>
          );
        }
        return <p key={bi} className="fleet-md-p">{renderMarkdownLiteInline(b.text || "", `${bi}`)}</p>;
      })}
    </>
  );
}

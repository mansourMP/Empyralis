"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface Message {
  role: "user" | "assistant";
  content: string;
}

export default function ChatPage() {
  const router = useRouter();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [authed, setAuthed] = useState<null | boolean>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch(`${API}/session`, { credentials: "include" })
      .then((r) => r.json())
      .then((data) => {
        setAuthed(data.authenticated);
        if (!data.authenticated) router.replace("/setup");
      })
      .catch(() => router.replace("/setup"));
  }, [router]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function handleSend() {
    const text = input.trim();
    if (!text || streaming) return;

    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: text }]);

    // Placeholder for the streaming response
    const asstIdx = messages.length + 1;
    setMessages((prev) => [...prev, { role: "assistant", content: "" }]);
    setStreaming(true);

    try {
      const res = await fetch(`${API}/chat`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Request failed");
      }

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const data = JSON.parse(line.slice(6));
            if (data.token) {
              setMessages((prev) => {
                const copy = [...prev];
                copy[asstIdx] = {
                  ...copy[asstIdx],
                  content: copy[asstIdx].content + data.token,
                };
                return copy;
              });
            } else if (data.error) {
              setMessages((prev) => {
                const copy = [...prev];
                copy[asstIdx] = {
                  ...copy[asstIdx],
                  content: `Error: ${data.error}`,
                };
                return copy;
              });
            }
            // data.done is ignored — streaming already complete
          } catch {
            // partial SSE line, skip
          }
        }
      }
    } catch (err: any) {
      setMessages((prev) => {
        const copy = [...prev];
        copy[asstIdx] = {
          ...copy[asstIdx],
          content: copy[asstIdx].content || `Error: ${err.message}`,
        };
        return copy;
      });
    } finally {
      setStreaming(false);
    }
  }

  if (authed === null) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <p className="text-zinc-400">Loading...</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-screen max-w-3xl mx-auto">
      {/* Header */}
      <header className="flex items-center justify-between px-4 py-3 border-b border-zinc-800">
        <h1 className="text-lg font-semibold text-zinc-100">Sage</h1>
        <div className="flex gap-3">
          <button
            onClick={() => router.push("/settings")}
            className="text-sm text-zinc-400 hover:text-zinc-200 transition-colors"
          >
            Settings
          </button>
        </div>
      </header>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-6 space-y-4">
        {messages.length === 0 && (
          <p className="text-zinc-500 text-center mt-12">
            Send a message to start.
          </p>
        )}
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[80%] rounded-lg px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap ${
                msg.role === "user"
                  ? "bg-zinc-100 text-zinc-900"
                  : "bg-zinc-900 text-zinc-200 border border-zinc-800"
              }`}
            >
              {msg.content || (streaming && i === messages.length - 1 ? "..." : "")}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="px-4 py-3 border-t border-zinc-800">
        <div className="flex gap-3">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSend()}
            placeholder="Send a message..."
            disabled={streaming}
            className="flex-1 px-4 py-2.5 bg-zinc-900 border border-zinc-700 rounded-lg
                       text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-zinc-500
                       disabled:opacity-50"
          />
          <button
            onClick={handleSend}
            disabled={streaming || !input.trim()}
            className="px-5 py-2.5 bg-zinc-100 text-zinc-900 rounded-lg font-medium
                       hover:bg-zinc-200 disabled:opacity-50 disabled:cursor-not-allowed
                       transition-colors"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}

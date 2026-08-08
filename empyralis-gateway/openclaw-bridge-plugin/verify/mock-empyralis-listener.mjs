import { createServer } from "node:http";
import { appendFileSync } from "node:fs";

const LOG_PATH = new URL("./received-events.jsonl", import.meta.url).pathname;

const server = createServer((req, res) => {
  let body = "";
  req.on("data", (chunk) => (body += chunk));
  req.on("end", () => {
    const hasAuth = Boolean(req.headers["authorization"]);
    console.log(`[mock-empyralis] ${req.method} ${req.url} auth=${hasAuth ? "present" : "absent"} <- ${body}`);
    try {
      appendFileSync(LOG_PATH, JSON.stringify({ ts: new Date().toISOString(), method: req.method, url: req.url, hasAuth, body: JSON.parse(body || "{}") }) + "\n");
    } catch (err) {
      console.error("[mock-empyralis] failed to persist event", err);
    }
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ ok: true }));
  });
});

server.listen(8790, "127.0.0.1", () => {
  console.log("[mock-empyralis] listening on http://127.0.0.1:8790/openclaw/inbound");
});

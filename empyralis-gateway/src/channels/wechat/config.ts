/** Env-var config loader for official WeChat, mirroring
 *  telegram/login.ts's loadTelegramLoginConfig — same
 *  EMPYRALIS_<CHANNEL>_<FIELD> naming convention, same "read once at
 *  startup, return undefined/empty for anything unset" contract. */
import type { WeChatAccountKind, WeChatOfficialConfig } from "./types";

function normalizeAccountKind(value: string | undefined): WeChatAccountKind {
  return String(value || "").trim().toLowerCase() === "wecom" ? "wecom" : "official_account";
}

/** Loads a WeChatOfficialConfig from process.env, or returns null if the
 *  required fields (appId/appSecret/token) aren't all present — same
 *  "absent config is not an error, it's just not configured" convention
 *  telegram/login.ts's loadTelegramLoginConfig follows via its optional
 *  fields. Callers decide what "not configured" means for them (e.g. a
 *  supervising process should simply not start startWeChatOfficialBridge
 *  at all rather than starting it with an unusable config). */
export function loadWeChatOfficialConfig(env: NodeJS.ProcessEnv = process.env): WeChatOfficialConfig | null {
  const accountKind = normalizeAccountKind(env.EMPYRALIS_WECHAT_ACCOUNT_KIND);
  const appId = String(env.EMPYRALIS_WECHAT_APP_ID || "").trim();
  const appSecret = String(env.EMPYRALIS_WECHAT_APP_SECRET || "").trim();
  const token = String(env.EMPYRALIS_WECHAT_VERIFY_TOKEN || "").trim();
  if (!appId || !appSecret || !token) {
    return null;
  }
  const agentId = String(env.EMPYRALIS_WECHAT_AGENT_ID || "").trim() || undefined;
  const encodingAesKey = String(env.EMPYRALIS_WECHAT_ENCODING_AES_KEY || "").trim() || undefined;
  return { accountKind, appId, appSecret, token, agentId, encodingAesKey };
}
